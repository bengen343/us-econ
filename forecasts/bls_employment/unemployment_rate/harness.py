r"""Walk-forward research harness for the unemployment-rate nowcast.

Read-only. Compares naive persistence baselines against regularised regressions
in BOTH framings the user asked for:

  * LEVEL models  -- target the rate directly.
  * CHANGE models -- target the MoM change, then add back last month's rate.

Everything is scored on the published LEVEL (rounded to 0.1), because the bar is
to "call it exactly": the headline metric is exact% (prediction rounds to the
same 0.1 as the print). COVID months (2020-03..2021-06) are masked.

Run: .\.venv\Scripts\python.exe -m forecasts.bls_employment.unemployment_rate.harness
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from forecasts.bls_employment import data
from forecasts.bls_employment.unemployment_rate import panel as panel_mod

TEST_START = pd.Timestamp("2011-01-01")
MIN_TRAIN = 36
ALPHAS = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0)


@dataclass
class Spec:
    name: str
    groups: tuple[str, ...]
    target: str  # "y_level" or "y_chg"


SPECS = [
    Spec("level: mom", ("momentum",), "y_level"),
    Spec("level: mom+iur", ("momentum", "iur"), "y_level"),
    Spec("level: mom+iur+claims", ("momentum", "iur", "claims"), "y_level"),
    Spec("level: mom+iur+claims+trends", ("momentum", "iur", "claims", "trends"), "y_level"),
    Spec("chg: iur", ("iur",), "y_chg"),
    Spec("chg: mom+iur", ("momentum", "iur"), "y_chg"),
    Spec("chg: mom+iur+claims", ("momentum", "iur", "claims"), "y_chg"),
    Spec("chg: mom+iur+claims+trends", ("momentum", "iur", "claims", "trends"), "y_chg"),
]


# --------------------------------------------------------------------------- #
# Metrics (all on the LEVEL)
# --------------------------------------------------------------------------- #
def score(true_lvl: np.ndarray, pred_lvl: np.ndarray, last_lvl: np.ndarray) -> dict[str, float]:
    err = pred_lvl - true_lvl
    ae = np.abs(err)
    true_chg = true_lvl - last_lvl
    pred_chg = pred_lvl - last_lvl
    moved = np.abs(true_chg) > 1e-9
    # Direction of the move, scored only on months that actually moved.
    dir_hit = (np.sign(np.round(pred_chg, 1)) == np.sign(true_chg))[moved]
    return {
        "n": len(err),
        "exact%": float(np.mean(np.round(pred_lvl, 1) == np.round(true_lvl, 1)) * 100),
        "<=0.1": float(np.mean(ae <= 0.1 + 1e-9) * 100),
        "<=0.2": float(np.mean(ae <= 0.2 + 1e-9) * 100),
        "MAE": float(np.mean(ae)),
        "RMSE": float(np.sqrt(np.mean(err**2))),
        "bias": float(np.mean(err)),
        "movedir%": float(np.mean(dir_hit) * 100) if moved.any() else float("nan"),
    }


# --------------------------------------------------------------------------- #
# Walk-forward
# --------------------------------------------------------------------------- #
def _obs(panel: pd.DataFrame) -> pd.DataFrame:
    return panel[(~panel["is_covid"]) & panel["y_level"].notna()]


def _test_months(panel: pd.DataFrame) -> list[pd.Timestamp]:
    o = _obs(panel)
    return [m for m in o.index if m >= TEST_START]


def walk_forward_baselines(panel: pd.DataFrame) -> pd.DataFrame:
    obs = _obs(panel)
    rows = []
    for m in _test_months(panel):
        hist = obs.loc[obs.index < m]
        if len(hist) < MIN_TRAIN:
            continue
        last = hist["y_level"].iloc[-1]
        drift = hist["y_chg"].iloc[-3:].mean()
        rows.append(
            {
                "month": m,
                "true": panel.at[m, "y_level"],
                "last": last,
                "rw": last,  # persistence (change = 0)
                "rw_drift": last + drift,  # persistence + recent drift
            }
        )
    return pd.DataFrame(rows).set_index("month")


def walk_forward_model(panel: pd.DataFrame, cols: list[str], target: str) -> pd.DataFrame:
    obs = _obs(panel)
    rows = []
    sub = panel[[target, "ur_lag1", *cols]]
    for m in _test_months(panel):
        train = sub.loc[(sub.index < m) & sub.index.isin(obs.index)].dropna()
        if len(train) < MIN_TRAIN:
            continue
        x_now = sub.loc[[m], cols]
        last = panel.at[m, "ur_lag1"]
        if x_now.isna().any(axis=None) or pd.isna(last):
            continue
        model = make_pipeline(StandardScaler(), RidgeCV(alphas=ALPHAS))
        model.fit(train[cols].values, train[target].values)
        raw = float(model.predict(x_now.values)[0])
        pred_lvl = raw if target == "y_level" else last + raw
        rows.append({"month": m, "true": panel.at[m, "y_level"], "last": last, "pred": pred_lvl})
    if not rows:
        return pd.DataFrame(columns=["true", "last", "pred"]).rename_axis("month")
    return pd.DataFrame(rows).set_index("month")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt(d: dict[str, float]) -> str:
    return (
        f"n={d['n']:>3.0f}  exact={d['exact%']:>4.0f}%  <=.1={d['<=0.1']:>4.0f}%  "
        f"<=.2={d['<=0.2']:>4.0f}%  MAE={d['MAE']:>5.3f}  RMSE={d['RMSE']:>5.3f}  "
        f"bias={d['bias']:>+6.3f}  movedir={d['movedir%']:>4.0f}%"
    )


def run() -> None:
    print("Pulling BigQuery inputs (read-only)...")
    c = data._client()
    panel, groups = panel_mod.build_panel(
        bls=data.pull_bls_series(panel_mod.BLS_SERIES, c),
        claims=data.pull_claims_national(c),
        trends=data.pull_trends(c),
    )
    obs = _obs(panel)
    tm = _test_months(panel)
    chg = obs.loc[obs.index >= TEST_START, "y_chg"]
    moved = (chg.abs() > 1e-9).mean() * 100
    print(f"\nPanel: {len(panel)} months; {len(obs)} non-COVID observed.")
    print(f"Test window: {len(tm)} origins {tm[0].date()}..{tm[-1].date()}.")
    print(
        f"UR MoM change in test window: mean {chg.mean():+.3f}  std {chg.std():.3f}  "
        f"moved (|chg|>0) {moved:.0f}% of months; mean|chg| {chg.abs().mean():.3f}pp.\n"
    )

    results: list[tuple[str, dict]] = []

    base = walk_forward_baselines(panel)
    print("=" * 104)
    print("BASELINES")
    print("=" * 104)
    for col in ("rw", "rw_drift"):
        s = score(base["true"].values, base[col].values, base["last"].values)
        results.append((f"baseline:{col}", s))
        print(f"  {col:<28} {_fmt(s)}")

    print("\n" + "=" * 104)
    print("RIDGE MODELS  (level- and change-target; scored on the rounded level)")
    print("=" * 104)
    for spec in SPECS:
        cols = [c for g in spec.groups for c in groups[g]]
        preds = walk_forward_model(panel, cols, spec.target).dropna(subset=["pred"])
        if preds.empty:
            print(f"  {spec.name:<28} (no valid origins)")
            continue
        s = score(preds["true"].values, preds["pred"].values, preds["last"].values)
        results.append((f"model:{spec.name}", s))
        print(f"  {spec.name:<28} {_fmt(s)}")

    print("\n" + "=" * 104)
    print("RANKED BY exact% (then MAE)  -- goal: call the 0.1 exactly")
    print("=" * 104)
    for name, s in sorted(results, key=lambda kv: (-kv[1]["exact%"], kv[1]["MAE"])):
        print(
            f"  {name:<34} exact={s['exact%']:>4.0f}%  <=.1={s['<=0.1']:>4.0f}%  "
            f"MAE={s['MAE']:>5.3f}  n={s['n']:>3.0f}"
        )

    _live_forecast(panel, groups)


def _live_forecast(panel: pd.DataFrame, groups: dict[str, list[str]]) -> None:
    live = panel[panel["y_level"].isna()]
    if live.empty:
        print("\nNo live (unreleased) month row.")
        return
    m = live.index.max()
    nxt = (m + pd.offsets.MonthBegin(1)).date()
    last = panel.at[m, "ur_lag1"]
    print("\n" + "=" * 104)
    print(
        f"LIVE FORECAST -> unemployment rate for {m.date()} "
        f"(released ~first Friday of {nxt}); last print {last:.1f}%"
    )
    print("=" * 104)
    obs = _obs(panel)
    for spec in (SPECS[2], SPECS[6]):  # level & change variants of mom+iur+claims
        cols = [c for g in spec.groups for c in groups[g]]
        train = panel.loc[panel.index.isin(obs.index), [spec.target, *cols]].dropna()
        x_now = panel.loc[[m], cols]
        if x_now.isna().any(axis=None):
            print(f"  {spec.name:<28} incomplete: {x_now.columns[x_now.isna().any()].tolist()}")
            continue
        model = make_pipeline(StandardScaler(), RidgeCV(alphas=ALPHAS))
        model.fit(train[cols].values, train[spec.target].values)
        raw = float(model.predict(x_now.values)[0])
        pred = raw if spec.target == "y_level" else last + raw
        print(f"  {spec.name:<28} {pred:.2f}%  (rounds to {np.round(pred, 1):.1f}%)")


if __name__ == "__main__":
    run()
