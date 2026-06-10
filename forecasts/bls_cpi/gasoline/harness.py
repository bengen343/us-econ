r"""Walk-forward bake-off: next-month CPI gasoline index (CUSR0000SETB01, SA).

Target: the about-to-be-released month M print, forecast just before the
mid-(M+1) CPI release, when month M's EIA weekly retail prices are fully
published -- the retail regressor is CONTEMPORANEOUS (unlike eggs, where
wholesale enters lagged). Modelled as Delta-log(SA index); levels recovered as
idx_{M-1} * exp(yhat).

Method candidates (literature review 2026-06 + repo prior art):

  * Baselines: random walk, expanding seasonal mean, AR(1).
  * Retail pass-through OLS (Cleveland-Fed-style; Knotek-Zaman use weekly EIA
    gasoline exactly this way): month-M retail change, with/without lag-1
    (carryover from intra-month timing) and the calendar-month SA wedge.
  * dms baseline: the deterministic form already in production for the CPI
    headline (forecasts/bls_cpi/dms) -- retail change minus the expanding
    NSA-SA gap, coefficient 1 imposed, translated to log space.
  * Oil/futures inputs were considered and dropped: Cleveland Fed needs them
    to extrapolate the UNFINISHED month; at our origin the month's retail
    prices are complete, so they have nothing left to explain.

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests are against the latest vintage; the SA series is re-seasonalised
~annually, so SA errors are mildly optimistic vs the true first print (same
caveat as the CPI dms harness).

Run: .\.venv\Scripts\python.exe -m forecasts.bls_cpi.gasoline.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bls_cpi.gasoline import data
from forecasts.bls_cpi.gasoline.model import MIN_TRAIN, build_features, forecast_next

CACHE = "_gas_panel.csv"  # scratch cache; delete to refetch
TEST_START = pd.Timestamp("2010-01-01")
COVID = (pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01"))


def wf_ols(p: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Expanding-window OLS of y on [1, cols], refit at every origin."""
    y = p["y"].to_numpy()
    X = np.column_stack([np.ones(len(p))] + [p[c].to_numpy() for c in cols])
    trainable = np.isfinite(X).all(axis=1) & np.isfinite(y)
    out = pd.Series(np.nan, index=p.index, dtype=float)
    for i in range(len(p)):
        if not np.isfinite(X[i]).all():
            continue
        train = trainable & (np.arange(len(p)) < i)
        if train.sum() < MIN_TRAIN:
            continue
        beta, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
        out.iloc[i] = float(X[i] @ beta)
    return out


SPECS_OLS: dict[str, list[str]] = {
    "ar1": ["dgas_1"],
    "eia": ["eia_mm"],
    "eia_wedge": ["eia_mm", "wedge"],
    "eia_dl": ["eia_mm", "eia_mm_1"],
    "eia_dl_wedge": ["eia_mm", "eia_mm_1", "wedge"],
    "eia_dl_ar_wedge": ["dgas_1", "eia_mm", "eia_mm_1", "wedge"],
    "eia_dl_seas": ["eia_mm", "eia_mm_1", "seas"],
}


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["rw"] = 0.0
    preds["seasonal"] = p["seas"]
    # dms-style deterministic pass-through: retail change minus the expanding
    # NSA-SA gap (coefficient 1 imposed; log-space analogue of the production
    # headline component). PIT: both terms are expanding/prior-shifted.
    preds["dms_determ"] = p["eia_mm"] + p["wedge"]
    for name, cols in SPECS_OLS.items():
        preds[name] = wf_ols(p, cols)
    ref = preds[list(SPECS_OLS)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p["y"], "yhat": yhat, "idx_lag": p["sa_idx"].shift(1)})
    d = d[mask].dropna()
    mm_err = (d["yhat"] - d["y"]) * 100  # log-points ~ pct m/m
    lvl_err = d["idx_lag"] * np.exp(d["yhat"]) - d["idx_lag"] * np.exp(d["y"])
    hit = np.sign(d["yhat"]) == np.sign(d["y"])
    return {
        "n": len(d),
        "mm_MAE": float(mm_err.abs().mean()),
        "mm_RMSE": float(np.sqrt((mm_err**2).mean())),
        "lvl_MAE": float(lvl_err.abs().mean()),
        "lvl_RMSE": float(np.sqrt((lvl_err**2).mean())),
        "bias": float(mm_err.mean()),
        "dir%": float(hit.mean() * 100),
    }


def run() -> None:
    print("Pulling CPI (BLS API) + EIA weekly (BigQuery), cached after first run...")
    panel = data.pull_panel(api_key=data.maybe_api_key(), cache=CACHE)
    p = build_features(panel)
    print(
        f"Panel {panel.index.min().date()}..{panel.index.max().date()} "
        f"(CPI n={panel['sa_idx'].notna().sum()}, "
        f"EIA complete months n={panel['eia'].notna().sum()})"
    )

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)

    scored = {name: score(p, preds[name], mask) for name in preds.columns}
    test_months = preds[mask].dropna(how="all").index
    print(
        f"\nTest window {test_months.min().date()}..{test_months.max().date()}, "
        f"COVID-masked. m/m errors in pct (log-points x100), levels in index points.\n"
    )
    header = (
        f"  {'method':<16} {'n':>4} {'mm_MAE':>7} {'mm_RMSE':>8} "
        f"{'lvl_MAE':>8} {'lvl_RMSE':>9} {'bias':>7} {'dir%':>6}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, s in sorted(scored.items(), key=lambda kv: kv[1]["mm_RMSE"]):
        print(
            f"  {name:<16} {s['n']:>4.0f} {s['mm_MAE']:>7.2f} {s['mm_RMSE']:>8.2f} "
            f"{s['lvl_MAE']:>8.3f} {s['lvl_RMSE']:>9.3f} {s['bias']:>+7.2f} {s['dir%']:>6.1f}"
        )

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec)")
    if live is None:
        print("  none (target-month regressors incomplete).")
    else:
        print(
            f"  {live.target_month.date()}: index {live.level:.3f} "
            f"({live.mm_pct:+.2f}% m/m SA, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
