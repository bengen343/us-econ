r"""Walk-forward bake-off: next-month new home sales (SAAR headline).

Target: new SF houses sold for month M (released ~the 23rd-27th of M+1),
modelled as Delta-log with levels recovered as sales_{M-1} * exp(yhat).
Scored in m/m % (log-points x100) and SAAR level (thousands). This is the
noisiest housing print (90% CI on m/m ~ +/-12pp; prelim revises ~5% on
average) -- the realistic bar is beating the random walk and the AR.

Method candidates (literature review 2026-06): mean-reverting own history;
SAME-MONTH SF permits/starts (the NRC release lands a week earlier and the
sales sample is permit-drawn); NAHB HMI and its SF-sales-present component
(month M, and the M+1 print that also precedes the release); the 30-yr
mortgage rate; months' supply at the end of M-1; LightGBM challenger.

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests use the latest vintage -- the ~5% average preliminary revision
makes this the most first-print-optimistic backtest in the repo (flagged in
the README; first prints accrue via the vintage-stamped collector).

Run: .\.venv\Scripts\python.exe -m forecasts.census_construction.new_home_sales.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.census_construction.new_home_sales import data
from forecasts.census_construction.new_home_sales.model import (
    MIN_TRAIN,
    build_features,
    forecast_next,
)

CACHE = "_nhs_panel.csv"  # scratch cache; delete to refetch
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


def wf_lgbm(p: pd.DataFrame, cols: list[str], refit_every: int = 12) -> pd.Series:
    """ML challenger (mirrors the other harnesses)."""
    import lightgbm as lgb

    feats = p[cols].copy()
    feats["month"] = p.index.month
    X = feats.to_numpy()
    y = p["y"].to_numpy()
    trainable = np.isfinite(X).all(axis=1) & np.isfinite(y)
    out = pd.Series(np.nan, index=p.index, dtype=float)
    model = None
    for j, i in enumerate(range(len(p))):
        if not np.isfinite(X[i]).all():
            continue
        train = trainable & (np.arange(len(p)) < i)
        if train.sum() < MIN_TRAIN:
            continue
        if model is None or j % refit_every == 0:
            model = lgb.LGBMRegressor(
                n_estimators=300,
                learning_rate=0.03,
                max_depth=3,
                min_child_samples=20,
                subsample=0.8,
                colsample_bytree=0.8,
                verbosity=-1,
                random_state=7,
            )
            model.fit(X[train], y[train])
        out.iloc[i] = float(model.predict(X[[i]])[0])
    return out


SPECS_OLS: dict[str, list[str]] = {
    "ar1": ["dnhs_1"],
    "ar2": ["dnhs_1", "dnhs_2"],
    "perm": ["dperm_0", "dnhs_1"],
    "perm_gap": ["perm_gap", "dnhs_1"],
    "perm_both": ["dperm_0", "perm_gap", "dnhs_1"],
    "starts0": ["dstart_0", "dnhs_1"],
    "hmi": ["dhmi_0", "dnhs_1"],
    "hmi_present": ["dhmip_0", "dnhs_1"],
    "hmi_lead": ["dhmi_lead", "dnhs_1"],
    "hmip_lead": ["dhmip_lead", "dnhs_1"],
    "mort": ["dmort_0", "dmort_t3", "dnhs_1"],
    "supply": ["supply_1", "dnhs_1"],
    "perm_hmi": ["dperm_0", "dhmip_0", "dnhs_1"],
    "perm_mort": ["dperm_0", "dmort_t3", "dnhs_1"],
    "perm_supply": ["dperm_0", "supply_1", "dnhs_1"],
    "kitchen": ["dperm_0", "perm_gap", "dhmip_0", "dmort_t3", "supply_1", "dnhs_1", "dnhs_2"],
}
LGBM_COLS = [
    "dperm_0",
    "perm_gap",
    "dstart_0",
    "dhmi_0",
    "dhmip_0",
    "dmort_0",
    "dmort_t3",
    "supply_1",
    "dsupply_1",
    "dnhs_1",
    "dnhs_2",
]


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["zero"] = 0.0
    for name, cols in SPECS_OLS.items():
        preds[name] = wf_ols(p, cols)
    preds["lgbm"] = wf_lgbm(p, LGBM_COLS)
    ref = preds[list(SPECS_OLS)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p["y"], "yhat": yhat, "lvl_lag": p["sales"].shift(1)})[mask].dropna()
    err = (d["yhat"] - d["y"]) * 100
    lvl_err = d["lvl_lag"] * np.exp(d["yhat"]) - d["lvl_lag"] * np.exp(d["y"])
    hit = np.sign(d["yhat"]) == np.sign(d["y"])
    return {
        "n": len(d),
        "mm_MAE": float(err.abs().mean()),
        "mm_RMSE": float(np.sqrt((err**2).mean())),
        "lvl_MAE": float(lvl_err.abs().mean()),
        "bias": float(err.mean()),
        "dir%": float(hit.mean() * 100),
    }


def run() -> None:
    print("Pulling Census/NAHB/Freddie official files, cached after first run...")
    panel = data.pull_panel(cache=CACHE)
    p = build_features(panel)
    avail = {c: int(panel[c].notna().sum()) for c in panel.columns}
    print(f"Panel coverage: {avail}")

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)
    mask_2017 = mask & (p.index >= pd.Timestamp("2017-01-01"))

    for label, window_mask in (("2010+", mask), ("2017+ subwindow", mask_2017)):
        scored = {name: score(p, preds[name], window_mask) for name in preds.columns}
        print(f"\n=== NEW HOME SALES m/m, % (log-points x100) [{label}] ===\n")
        header = (
            f"  {'method':<13} {'n':>4} {'mm_MAE':>7} {'mm_RMSE':>8} {'lvl_MAE':>8} "
            f"{'bias':>7} {'dir%':>6}"
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for name, s in sorted(scored.items(), key=lambda kv: kv[1]["mm_RMSE"]):
            print(
                f"  {name:<13} {s['n']:>4.0f} {s['mm_MAE']:>7.2f} {s['mm_RMSE']:>8.2f} "
                f"{s['lvl_MAE']:>8.1f} {s['bias']:>+7.2f} {s['dir%']:>6.1f}"
            )

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec)")
    if live is None:
        print("  none (target-month regressors incomplete)")
    else:
        print(
            f"  {live.target_month.date()}: {live.level:.0f}k SAAR "
            f"({live.mm_pct:+.1f}% m/m, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
