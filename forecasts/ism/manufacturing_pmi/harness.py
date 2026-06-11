r"""Walk-forward bake-off: next-month ISM Manufacturing PMI.

Target: the headline PMI for month M (released the 1st business day of M+1),
modelled as the m/m CHANGE with levels recovered as pmi_{M-1} + yhat. Scored
in PMI points. The services-ISM research (2026-06) put the realistic frontier
at "AR(1) is strong, single-point RMSE ~2 is hard to beat by much".

Method candidates: own history (AR, mean reversion toward 50, the
new-orders-minus-headline lead); month-M regional Fed surveys mapped to the
ISM scale (Empire/Philly/Richmond/Dallas, correlations 0.71-0.83); month-M
Chicago PMI (last business day of M -- subscriber-gated source, tested to
price what it's worth); the month-M S&P Global flash (from 2012 -- short
history, so flash specs fit with MIN_TRAIN_SHORT=60 and are compared on the
flash-era window); LightGBM challenger.

Two leaderboards: the long window (2010+) for specs with deep history, and
the flash-era window (2018+) where every spec competes. COVID months
(2020-03..2021-06) are masked from scoring per repo convention. The ISM is
re-seasonally-adjusted annually (small revisions; latest-vintage backtest).

Run: .\.venv\Scripts\python.exe -m forecasts.ism.manufacturing_pmi.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.ism.manufacturing_pmi import data
from forecasts.ism.manufacturing_pmi.model import (
    MIN_TRAIN,
    MIN_TRAIN_SHORT,
    build_features,
    forecast_next,
)

CACHE = "_ism_panel.csv"  # scratch cache; delete to refetch
TEST_START = pd.Timestamp("2010-01-01")
FLASH_TEST_START = pd.Timestamp("2018-01-01")
COVID = (pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01"))


def wf_ols(p: pd.DataFrame, cols: list[str], min_train: int = MIN_TRAIN) -> pd.Series:
    """Expanding-window OLS of y on [1, cols], refit at every origin."""
    y = p["y"].to_numpy()
    X = np.column_stack([np.ones(len(p))] + [p[c].to_numpy() for c in cols])
    trainable = np.isfinite(X).all(axis=1) & np.isfinite(y)
    out = pd.Series(np.nan, index=p.index, dtype=float)
    for i in range(len(p)):
        if not np.isfinite(X[i]).all():
            continue
        train = trainable & (np.arange(len(p)) < i)
        if train.sum() < min_train:
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


# Long-history specs (Chicago 1967+, Philly 1968+; Empire/Richmond 2001+,
# Dallas 2004+ bind the regional combos).
SPECS_LONG: dict[str, list[str]] = {
    "ar1": ["dpmi_1"],
    "ar2": ["dpmi_1", "dpmi_2"],
    "meanrev": ["dpmi_1", "pmi50_1"],
    "orders": ["dpmi_1", "orders_gap_1"],
    "chi": ["chi_gap", "dpmi_1"],
    "philly": ["phl_gap", "dpmi_1"],
    "chi_philly": ["chi_gap", "phl_gap", "dpmi_1"],
    "empire": ["emp_gap", "dpmi_1"],
    "richmond": ["ric_gap", "dpmi_1"],
    "fed4": ["fed_gap", "dpmi_1"],
    "fed4_chi": ["fed_gap", "chi_gap", "dpmi_1"],
    "fed4_chi_ord": ["fed_gap", "chi_gap", "dpmi_1", "orders_gap_1"],
    "kitchen": [
        "chi_gap",
        "phl_gap",
        "emp_gap",
        "ric_gap",
        "dal_gap",
        "dpmi_1",
        "orders_gap_1",
        "pmi50_1",
    ],
}
# Flash-era specs (S&P flash from 2012-06; MIN_TRAIN_SHORT).
SPECS_FLASH: dict[str, list[str]] = {
    "flash": ["fl_gap", "dpmi_1"],
    "flash_chi": ["fl_gap", "chi_gap", "dpmi_1"],
    "flash_fed": ["fl_gap", "fed_gap", "dpmi_1"],
    "flash_fed_chi": ["fl_gap", "fed_gap", "chi_gap", "dpmi_1"],
}
LGBM_COLS = [
    "chi_gap",
    "phl_gap",
    "emp_gap",
    "ric_gap",
    "dal_gap",
    "fed_gap",
    "dpmi_1",
    "dpmi_2",
    "orders_gap_1",
    "pmi50_1",
]


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["rw"] = 0.0
    for name, cols in SPECS_LONG.items():
        preds[name] = wf_ols(p, cols)
    for name, cols in SPECS_FLASH.items():
        preds[name] = wf_ols(p, cols, min_train=MIN_TRAIN_SHORT)
    preds["lgbm"] = wf_lgbm(p, LGBM_COLS)
    return preds


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p["y"], "yhat": yhat})[mask].dropna()
    err = d["yhat"] - d["y"]
    hit = np.sign(d["yhat"]) == np.sign(d["y"])
    return {
        "n": len(d),
        "MAE": float(err.abs().mean()),
        "RMSE": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "dir%": float(hit.mean() * 100),
    }


def _print_board(p: pd.DataFrame, preds: pd.DataFrame, names: list[str], mask: pd.Series) -> None:
    # Identical months within the board: every listed method non-NaN.
    ref = preds[names].notna().all(axis=1) & mask
    scored = {name: score(p, preds[name], ref) for name in names}
    header = f"  {'method':<15} {'n':>4} {'MAE':>6} {'RMSE':>7} {'bias':>7} {'dir%':>6}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, s in sorted(scored.items(), key=lambda kv: kv[1]["RMSE"]):
        print(
            f"  {name:<15} {s['n']:>4.0f} {s['MAE']:>6.2f} {s['RMSE']:>7.2f} "
            f"{s['bias']:>+7.2f} {s['dir%']:>6.1f}"
        )


def run() -> None:
    print("Pulling ISM (BigQuery) + survey workbook (Sism.xlsm), cached after first run...")
    panel = data.pull_panel(cache=CACHE)
    p = build_features(panel)
    avail = {c: int(panel[c].notna().sum()) for c in panel.columns}
    print(f"Panel coverage: {avail}")

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    long_mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)
    flash_mask = pd.Series((p.index >= FLASH_TEST_START) & ~is_covid, index=p.index)

    long_names = ["rw", *SPECS_LONG, "lgbm"]
    all_names = ["rw", *SPECS_LONG, *SPECS_FLASH, "lgbm"]
    print("\n=== ISM MANUFACTURING PMI change, points [2010+, long-history specs] ===\n")
    _print_board(p, preds, long_names, long_mask)
    print("\n=== ISM MANUFACTURING PMI change, points [2018+, all specs] ===\n")
    _print_board(p, preds, all_names, flash_mask)

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec)")
    if live is None:
        print("  none (month-M surveys not yet available -- workbook ends 2025-12)")
    else:
        print(
            f"  {live.target_month.date()}: PMI {live.level:.1f} "
            f"({live.change:+.1f} vs prior, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
