r"""Walk-forward research harness: Cleveland-Fed-style bottom-up CPI nowcast.

Read-only. Replicates the Knotek-Zaman deterministic reconstruction: rebuild the
headline from weighted components, each nowcast deterministically --

  * core (all items less food & energy) m/m  = trailing 12-month average
  * food m/m                                 = trailing 12-month average
  * energy m/m  = gasoline (from the high-frequency monthly retail price,
                  deseasonalised with the empirical NSA-SA gap) + non-gasoline
                  energy as a trailing 12-month average, weighted within energy
  * headline m/m = w_core*core + w_food*food + w_energy*energy   (RI weights)

Everything is done in published-SA space, so no projected seasonal factors are
needed. The four targets:

  * headline m/m (SA)   <- reconstructed headline
  * core m/m (SA)       <- the core piece (= trailing average)
  * headline y/y        <- chain the SA m/m onto the SA index, 12-month change
  * core y/y            <- same for core
    (the SA 12-month change ~ the published NSA y/y; seasonality cancels.)

The reconstruction is deterministic (no fitting), so the nowcasts are computed
vectorised over the whole panel using only information dated <= M-1 (plus month
M's complete fuel prices), then scored on a held-out window. COVID months
(2020-03 .. 2021-06) are masked from the error stats.

Caveats: backtests against the latest vintage (NSA y/y is effectively final; SA
m/m is re-seasonalised ~annually, so SA m/m errors are mildly optimistic vs the
true first print). Aggregation uses the current-year RI weights for all history
(weights drift slowly; price-updating them is the obvious next refinement).

Run: .\.venv\Scripts\python.exe -m forecasts.bls_cpi.dms.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bls_cpi import data
from forecasts.bls_cpi.dms import panel as panel_mod

TEST_START = pd.Timestamp("2010-01-01")

TARGETS = {
    "headline_mm": "Headline m/m (SA, pp)",
    "core_mm": "Core m/m (SA, pp)",
    "headline_yy": "Headline y/y (pp)",
    "core_yy": "Core y/y (pp)",
}


# --------------------------------------------------------------------------- #
# Reconstruction + baselines (vectorised; all inputs dated <= M-1 except month
# M's complete fuel prices)
# --------------------------------------------------------------------------- #
def _trailing12(s: pd.Series) -> pd.Series:
    # min_periods=9 tolerates the 2-month 2025 appropriations-lapse gap (Oct +
    # Nov-m/m missing) without blanking a year of forecasts. Safe because these
    # are SA series, so averaging over the available months isn't seasonally
    # biased -- the trailing mean is just a slow trend level.
    return s.shift(1).rolling(12, min_periods=9).mean()


def build_forecasts(
    panel: pd.DataFrame, weights: dict[str, float], weight_year: int
) -> pd.DataFrame:
    p = panel

    # Time-varying relative importances via the BLS price-update identity: the
    # base-year (December `weight_year`) cost weight, price-updated by each item's
    # NSA index relative to that December base. Using the prior month's (t-1) NSA
    # index keeps it PIT-clean and matches the CPI contribution convention
    # (RI_{t-1} weights the change into month t). Normalising over the
    # core/food/energy partition gives fractions summing to 1; this lets energy's
    # weight rise when fuel prices spike -- exactly when static weights misfire.
    base = pd.Timestamp(weight_year, 12, 1)

    def updated_weight(short: str, code: str) -> pd.Series:
        return weights[code] * p[f"{short}_nsa_idx"].shift(1) / p.at[base, f"{short}_nsa_idx"]

    uw_core = updated_weight("core", "SA0L1E")
    uw_food = updated_weight("food", "SAF1")
    uw_energy = updated_weight("energy", "SA0E")
    uw_gas = updated_weight("gas", "SETB01")
    denom = uw_core + uw_food + uw_energy
    w_core, w_food, w_energy = uw_core / denom, uw_food / denom, uw_energy / denom
    f_gas_in_energy = uw_gas / uw_energy  # gasoline share of energy (time-varying)

    f = pd.DataFrame(index=p.index)

    # ---- component nowcasts ------------------------------------------------ #
    core_trail = _trailing12(p["core_sa_mm"])
    food_trail = _trailing12(p["food_sa_mm"])
    enserv_trail = _trailing12(p["enserv_sa_mm"])

    # gasoline: monthly retail price change ~ CPI gasoline NSA m/m; deseasonalise
    # with the expanding mean NSA-SA gap for that calendar month (prior months only).
    gap = p["gas_nsa_mm"] - p["gas_sa_mm"]
    gas_seas = gap.groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    gas_sa_hat = p["eia_gas_mm"] - gas_seas
    energy_hat = f_gas_in_energy * gas_sa_hat + (1.0 - f_gas_in_energy) * enserv_trail

    # ---- target nowcasts --------------------------------------------------- #
    f["headline_mm"] = w_core * core_trail + w_food * food_trail + w_energy * energy_hat
    f["core_mm"] = core_trail
    # y/y: chain the SA m/m onto the known SA index, take the 12-month change.
    f["headline_yy"] = p["all_sa_idx"].shift(1) * (1 + f["headline_mm"] / 100) / p[
        "all_sa_idx"
    ].shift(12) * 100 - 100
    f["core_yy"] = p["core_sa_idx"].shift(1) * (1 + f["core_mm"] / 100) / p["core_sa_idx"].shift(
        12
    ) * 100 - 100

    # ---- baselines --------------------------------------------------------- #
    f["headline_mm__rw"] = p["all_sa_mm"].shift(1)
    f["headline_mm__trail12"] = _trailing12(p["all_sa_mm"])
    f["headline_mm__zero"] = 0.0
    f["core_mm__rw"] = p["core_sa_mm"].shift(1)
    f["core_mm__trail12"] = _trailing12(p["core_sa_mm"])
    f["headline_yy__rw"] = p["all_nsa_yy"].shift(1)
    f["core_yy__rw"] = p["core_nsa_yy"].shift(1)

    # ---- actuals ----------------------------------------------------------- #
    f["headline_mm__actual"] = p["all_sa_mm"]
    f["core_mm__actual"] = p["core_sa_mm"]
    f["headline_yy__actual"] = p["all_nsa_yy"]
    f["core_yy__actual"] = p["core_nsa_yy"]
    f["is_covid"] = p["is_covid"]
    return f


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score(actual: pd.Series, pred: pd.Series) -> dict[str, float]:
    d = pd.concat([actual.rename("a"), pred.rename("p")], axis=1).dropna()
    err = (d["p"] - d["a"]).values
    ae = np.abs(err)
    return {
        "n": len(err),
        "MAE": float(np.mean(ae)) if len(err) else np.nan,
        "RMSE": float(np.sqrt(np.mean(err**2))) if len(err) else np.nan,
        "MedAE": float(np.median(ae)) if len(err) else np.nan,
        "bias": float(np.mean(err)) if len(err) else np.nan,
        "%<0.1": float(np.mean(ae <= 0.1 + 1e-9) * 100) if len(err) else np.nan,
    }


def _fmt(name: str, s: dict[str, float]) -> str:
    return (
        f"  {name:<22} n={s['n']:>3.0f}  MAE={s['MAE']:.3f}  RMSE={s['RMSE']:.3f}  "
        f"Med={s['MedAE']:.3f}  bias={s['bias']:+.3f}  %<0.1pp={s['%<0.1']:>4.0f}"
    )


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run() -> None:
    print("Pulling BigQuery inputs (read-only)...")
    c = data._client()
    panel = panel_mod.build_panel(data.pull_cpi(c), data.pull_eia_monthly(c))
    weights, weight_year = data.pull_cpi_weights(c)
    f = build_forecasts(panel, weights, weight_year)

    test = f[(f.index >= TEST_START) & (~f["is_covid"].fillna(False))]
    last_actual = panel["all_sa_mm"].last_valid_index()
    print(
        f"\nPanel {panel.index.min().date()}..{panel.index.max().date()}; "
        f"last CPI actual {last_actual.date()}; "
        f"test window {test.index.min().date()}..{test.index.max().date()} "
        f"(>= {TEST_START.date()}, COVID-masked).\n"
    )

    methods = {
        "headline_mm": [
            "headline_mm",
            "headline_mm__trail12",
            "headline_mm__rw",
            "headline_mm__zero",
        ],
        "core_mm": ["core_mm", "core_mm__trail12", "core_mm__rw"],
        "headline_yy": ["headline_yy", "headline_yy__rw"],
        "core_yy": ["core_yy", "core_yy__rw"],
    }

    for target, cols in methods.items():
        print("=" * 92)
        print(f"{TARGETS[target]}")
        print("=" * 92)
        actual = test[f"{target}__actual"]
        scored = []
        for col in cols:
            tag = "reconstruction" if col == target else col.split("__", 1)[1]
            scored.append((tag, score(actual, test[col])))
        for tag, s in sorted(scored, key=lambda kv: kv[1]["RMSE"]):
            marker = "  <-- bottom-up" if tag == "reconstruction" else ""
            print(_fmt(tag, s) + marker)
        print()

    _live(panel, f, last_actual)


def _live(panel: pd.DataFrame, f: pd.DataFrame, last_actual: pd.Timestamp) -> None:
    live = [m for m in f.index if m > last_actual and pd.notna(f.at[m, "headline_mm"])]
    print("=" * 92)
    print("LIVE NOWCASTS (months not yet released; complete fuel prices assumed)")
    print("=" * 92)
    if not live:
        print("  none (no month with complete inputs past the last release).")
        return
    for m in live:
        nxt = (m + pd.offsets.MonthBegin(1)).date()
        print(f"\n  {m.date()}  (released ~mid-{nxt:%B %Y})")
        for t in TARGETS:
            v = f.at[m, t]
            shown = f"{v:+.2f}" if pd.notna(v) else "n/a"
            print(f"     {TARGETS[t]:<24} {shown}")


if __name__ == "__main__":
    run()
