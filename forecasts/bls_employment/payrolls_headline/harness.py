r"""Walk-forward research harness for the NFP headline nowcast.

Read-only. Pulls BigQuery, builds the monthly panel, runs an expanding-window
walk-forward backtest comparing naive baselines (incl. an ADP-direct bridge)
against regularised regressions over ablated feature groups, and prints an
honest scorecard (MAE / RMSE / median / % within 10k / direction hit-rate) plus
a live forecast for the next unreleased month.

COVID shock months (2020-03 .. 2021-06) are masked out of both training and the
test window — they are unforecastable outliers that otherwise dominate every
error metric on a series whose normal monthly change is ~150k but which printed
-20,787k in Apr-2020.

Run: .\.venv\Scripts\python.exe -m forecasts.bls_employment.payrolls_headline.harness
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from forecasts.bls_employment import data
from forecasts.bls_employment.payrolls_headline import panel as panel_mod

TEST_START = pd.Timestamp("2011-01-01")  # post-GFC; leaves 5y of momentum history
MIN_TRAIN = 36
GOAL = 10  # user's stretch: NFP MoM within 10k (series units = thousands)
ALPHAS = (0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0)


@dataclass
class Spec:
    name: str
    groups: tuple[str, ...]


SPECS = [
    Spec("momentum", ("momentum",)),
    Spec("claims", ("claims",)),
    Spec("adp", ("adp",)),
    Spec("trends", ("trends",)),
    Spec("temphelp", ("temphelp",)),
    Spec("mom+claims", ("momentum", "claims")),
    Spec("mom+adp", ("momentum", "adp")),
    Spec("mom+claims+temphelp", ("momentum", "claims", "temphelp")),
    Spec("mom+claims+adp", ("momentum", "claims", "adp")),
    Spec("mom+claims+adp+trends", ("momentum", "claims", "adp", "trends")),
    Spec("all", ("momentum", "claims", "adp", "trends", "temphelp")),
]


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def score(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    err = y_pred - y_true
    ae = np.abs(err)
    return {
        "n": len(err),
        "MAE": float(np.mean(ae)),
        "RMSE": float(np.sqrt(np.mean(err**2))),
        "MedAE": float(np.median(ae)),
        "bias": float(np.mean(err)),
        f"%<{GOAL}k": float(np.mean(ae <= GOAL) * 100),
        "%<25k": float(np.mean(ae <= 25) * 100),
        "%<50k": float(np.mean(ae <= 50) * 100),
        "dir%": float(np.mean((y_pred > 0) == (y_true > 0)) * 100),
    }


# --------------------------------------------------------------------------- #
# Walk-forward
# --------------------------------------------------------------------------- #
def _obs(panel: pd.DataFrame) -> pd.DataFrame:
    """Observed, non-COVID months with a target (training/baseline universe)."""
    return panel[(~panel["is_covid"]) & panel["y"].notna()]


def _test_months(panel: pd.DataFrame) -> list[pd.Timestamp]:
    o = _obs(panel)
    return [m for m in o.index if m >= TEST_START]


def walk_forward_baselines(panel: pd.DataFrame) -> pd.DataFrame:
    obs = _obs(panel)
    rows = []
    for m in _test_months(panel):
        hist = obs.loc[obs.index < m, "y"]
        if len(hist) < MIN_TRAIN:
            continue
        snaive = panel["y"].get(m - pd.offsets.MonthBegin(12), np.nan)
        if pd.isna(snaive):
            snaive = hist.iloc[-1]
        rows.append(
            {
                "month": m,
                "y_true": panel.at[m, "y"],
                "rw": hist.iloc[-1],
                "mean3": hist.iloc[-3:].mean(),
                "longrun": hist.mean(),
                "snaive": snaive,
                "zero": 0.0,
                "adp_direct": panel.at[m, "adp_chg"],  # ADP headline as the forecast
            }
        )
    return pd.DataFrame(rows).set_index("month")


def walk_forward_model(panel: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    obs = _obs(panel)
    rows = []
    sub = panel[["y", *cols]]
    for m in _test_months(panel):
        train = sub.loc[(sub.index < m) & sub.index.isin(obs.index)].dropna()
        if len(train) < MIN_TRAIN:
            continue
        x_now = sub.loc[[m], cols]
        if x_now.isna().any(axis=None):
            continue
        model = make_pipeline(StandardScaler(), RidgeCV(alphas=ALPHAS))
        model.fit(train[cols].values, train["y"].values)
        rows.append(
            {
                "month": m,
                "y_true": panel.at[m, "y"],
                "pred": float(model.predict(x_now.values)[0]),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["y_true", "pred"]).rename_axis("month")
    return pd.DataFrame(rows).set_index("month")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt(d: dict[str, float]) -> str:
    return (
        f"n={d['n']:>3.0f}  MAE={d['MAE']:>6,.1f}  RMSE={d['RMSE']:>6,.1f}  "
        f"Med={d['MedAE']:>5,.1f}  bias={d['bias']:>+6,.1f}  "
        f"%<{GOAL}k={d[f'%<{GOAL}k']:>4.0f}  %<25k={d['%<25k']:>4.0f}  "
        f"%<50k={d['%<50k']:>4.0f}  dir%={d['dir%']:>4.0f}"
    )


def run() -> None:
    print("Pulling BigQuery inputs (read-only)...")
    c = data._client()
    panel, groups = panel_mod.build_panel(
        bls=data.pull_bls_series(panel_mod.BLS_SERIES, c),
        claims=data.pull_claims_national(c),
        adp=data.pull_adp_monthly(c),
        pulse=data.pull_adp_pulse(c),
        trends=data.pull_trends(c),
        challenger=data.pull_challenger(c),
    )

    obs = _obs(panel)
    tm = _test_months(panel)
    y = obs["y"]
    yt = obs.loc[obs.index >= TEST_START, "y"]
    print(
        f"\nPanel: {len(panel)} months ({panel.index.min().date()} .. "
        f"{panel.index.max().date()}); {len(obs)} non-COVID observed."
    )
    print(
        f"Target NFP MoM change (k)  full 2006+: mean {y.mean():,.0f}  std {y.std():,.0f}  "
        f"mean|.| {y.abs().mean():,.0f}"
    )
    print(
        f"                        test window: mean {yt.mean():,.0f}  std {yt.std():,.0f}  "
        f"mean|.| {yt.abs().mean():,.0f}"
    )
    print(
        f"Test window: {len(tm)} origins {tm[0].date()}..{tm[-1].date()} "
        f"(>= {TEST_START.date()}, COVID-masked, >= {MIN_TRAIN} train).\n"
    )

    results: list[tuple[str, dict]] = []

    base = walk_forward_baselines(panel)
    print("=" * 104)
    print("BASELINES")
    print("=" * 104)
    for col in ("rw", "mean3", "longrun", "snaive", "zero", "adp_direct"):
        aligned = base.dropna(subset=[col])
        s = score(aligned["y_true"].values, aligned[col].values)
        results.append((f"baseline:{col}", s))
        print(f"  {col:<22} {_fmt(s)}")

    print("\n" + "=" * 104)
    print("RIDGE MODELS  (RidgeCV, expanding window; each spec scored on its own valid origins)")
    print("=" * 104)
    for spec in SPECS:
        cols = [c for g in spec.groups for c in groups[g]]
        preds = walk_forward_model(panel, cols).dropna(subset=["pred", "y_true"])
        if preds.empty:
            print(f"  {spec.name:<22} (no valid origins)")
            continue
        s = score(preds["y_true"].values, preds["pred"].values)
        results.append((f"model:{spec.name}", s))
        print(f"  {spec.name:<22} {_fmt(s)}")

    print("\n" + "=" * 104)
    print(f"RANKED BY MAE  (goal: MAE <= {GOAL}k)")
    print("=" * 104)
    for name, s in sorted(results, key=lambda kv: kv[1]["MAE"]):
        flag = "  <-- meets goal" if s["MAE"] <= GOAL else ""
        print(
            f"  {name:<28} MAE={s['MAE']:>6,.1f}  RMSE={s['RMSE']:>6,.1f}  "
            f"n={s['n']:>3.0f}  %<{GOAL}k={s[f'%<{GOAL}k']:>4.0f}{flag}"
        )

    _live_forecast(panel, groups)


def _live_forecast(panel: pd.DataFrame, groups: dict[str, list[str]]) -> None:
    live = panel[panel["y"].isna()]
    if live.empty:
        print("\nNo live (unreleased) month row.")
        return
    m = live.index.max()
    nxt = (m + pd.offsets.MonthBegin(1)).date()
    print("\n" + "=" * 104)
    print(f"LIVE FORECAST -> NFP headline for {m.date()} (released ~first Friday of {nxt})")
    print("=" * 104)
    obs = _obs(panel)
    for name, gset in [
        ("mom+claims", ("momentum", "claims")),
        ("mom+claims+adp", ("momentum", "claims", "adp")),
    ]:
        cols = [c for g in gset for c in groups[g]]
        train = panel.loc[panel.index.isin(obs.index), ["y", *cols]].dropna()
        x_now = panel.loc[[m], cols]
        if x_now.isna().any(axis=None):
            miss = x_now.columns[x_now.isna().any()].tolist()
            print(f"  {name:<16} incomplete features: {miss}")
            continue
        model = make_pipeline(StandardScaler(), RidgeCV(alphas=ALPHAS))
        model.fit(train[cols].values, train["y"].values)
        print(f"  {name:<16} {float(model.predict(x_now.values)[0]):>+7,.0f}k")
    if pd.notna(panel.at[m, "adp_chg"]):
        print(f"  {'adp_direct':<16} {panel.at[m, 'adp_chg']:>+7,.0f}k")


if __name__ == "__main__":
    run()
