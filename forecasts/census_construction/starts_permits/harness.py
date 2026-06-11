r"""Walk-forward bake-offs: next-month housing starts + building permits.

Targets: Delta-log of the headline SAAR totals for report month M, forecast
just before the joint New Residential Construction release (~17th of M+1).
Scored in m/m % (log-points x100) and SAAR level (thousands).

Method candidates (literature review 2026-06): persistence baselines; the
permits->starts ECM (lagged log permits/starts gap); lagged permit changes;
a single-family/multifamily bottom-up recomposition (SF starts track SF
permits within ~2 months; 5+ is lumpy); NAHB HMI month M (Fed-validated
starts predictor, released before the origin); 30-yr mortgage rate changes;
NOAA temperature deviation from the calendar-month norm (winter noise);
LightGBM challenger.

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests use the latest vintage; starts/permits are revised for two months
after first print (avg revision <= ~2.9%) -- mildly optimistic vs first
prints, noted in the README.

Run: .\.venv\Scripts\python.exe -m forecasts.census_construction.starts_permits.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.census_construction.starts_permits import data
from forecasts.census_construction.starts_permits.model import (
    MIN_TRAIN,
    build_features,
    forecast_permits,
    forecast_starts,
)

CACHE = "_constr_panel.csv"  # scratch cache; delete to refetch
TEST_START = pd.Timestamp("2010-01-01")
COVID = (pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01"))


def wf_ols(p: pd.DataFrame, y_col: str, cols: list[str]) -> pd.Series:
    """Expanding-window OLS of y on [1, cols], refit at every origin."""
    y = p[y_col].to_numpy()
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


def wf_lgbm(p: pd.DataFrame, y_col: str, cols: list[str], refit_every: int = 12) -> pd.Series:
    """ML challenger (mirrors the other harnesses)."""
    import lightgbm as lgb

    feats = p[cols].copy()
    feats["month"] = p.index.month
    X = feats.to_numpy()
    y = p[y_col].to_numpy()
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


def wf_bottom_up(p: pd.DataFrame) -> pd.Series:
    """Forecast SF and 5+ starts separately, carry 2-4 forward, recompose the
    total -- expressed as an implied Delta-log of the total. The published
    2-4 column is mostly suppressed '(S)' in the SA sheet, so the 2-4 carry
    is the residual implied by the totals."""
    sf = wf_ols(p, "y_sfst", ["sfgap_1", "dsfpm_1", "dsfst_1"])
    mf = wf_ols(p, "y_mfst", ["mfgap_1", "dmfst_1"])
    sf_hat = p["st_sf"].shift(1) * np.exp(sf)
    mf_hat = p["st_mf5"].shift(1) * np.exp(mf)
    mf24_carry = (p["st_total"] - p["st_sf"] - p["st_mf5"]).clip(lower=0).shift(1)
    total_hat = sf_hat + mf_hat + mf24_carry
    return np.log(total_hat) - np.log(p["st_total"].shift(1))


SPECS_STARTS: dict[str, list[str]] = {
    "ar1": ["dst_1"],
    "ar2": ["dst_1", "dst_2"],
    "pm_bridge": ["dpm_1", "dpm_2"],
    "ecm": ["gap_1"],
    "ecm_ar": ["gap_1", "dst_1"],
    "ecm_pm": ["gap_1", "dpm_1"],
    "hmi": ["dhmi_0", "dhmi_1"],
    "mort": ["dmort_0", "dmort_t3"],
    "weather": ["tdev_0", "tdev_1"],
    "ecm_hmi": ["gap_1", "dst_1", "dhmi_0"],
    "ecm_weather": ["gap_1", "dst_1", "tdev_0", "tdev_1"],
    "ecm_hmi_wx": ["gap_1", "dst_1", "dhmi_0", "tdev_0", "tdev_1"],
    "kitchen_st": ["gap_1", "dst_1", "dpm_1", "dhmi_0", "dmort_t3", "tdev_0", "tdev_1"],
}
LGBM_STARTS = [
    "gap_1",
    "dst_1",
    "dst_2",
    "dpm_1",
    "dpm_2",
    "sfgap_1",
    "dsfpm_1",
    "dhmi_0",
    "dhmi_1",
    "dmort_0",
    "dmort_t3",
    "tdev_0",
    "tdev_1",
]

SPECS_PERMITS: dict[str, list[str]] = {
    "ar1": ["dpm_1"],
    "ar2": ["dpm_1", "dpm_2"],
    "sf_mf": ["dsfpm_1", "dmfpm_1"],
    "hmi": ["dhmi_0", "dhmi_1"],
    "mort": ["dmort_0", "dmort_t3"],
    "ar_hmi": ["dpm_1", "dhmi_0"],
    "ar_mort": ["dpm_1", "dmort_0", "dmort_t3"],
    "ar_hmi_mort": ["dpm_1", "dhmi_0", "dmort_t3"],
    "kitchen_pm": ["dpm_1", "dpm_2", "dhmi_0", "dmort_0", "dmort_t3", "tdev_0"],
}
LGBM_PERMITS = [
    "dpm_1",
    "dpm_2",
    "dsfpm_1",
    "dmfpm_1",
    "dhmi_0",
    "dhmi_1",
    "dmort_0",
    "dmort_1",
    "dmort_t3",
    "tdev_0",
]


def run_board(
    p: pd.DataFrame,
    y_col: str,
    level_col: str,
    specs: dict[str, list[str]],
    lgbm_cols: list[str],
    bottom_up: bool = False,
) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["zero"] = 0.0
    for name, cols in specs.items():
        preds[name] = wf_ols(p, y_col, cols)
    if bottom_up:
        preds["bottom_up"] = wf_bottom_up(p)
    preds["lgbm"] = wf_lgbm(p, y_col, lgbm_cols)
    ref = preds[list(specs)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(
    p: pd.DataFrame, y_col: str, level_col: str, yhat: pd.Series, mask: pd.Series
) -> dict[str, float]:
    d = pd.DataFrame({"y": p[y_col], "yhat": yhat, "lvl_lag": p[level_col].shift(1)})[mask].dropna()
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


def _print_board(
    p: pd.DataFrame, y_col: str, level_col: str, preds: pd.DataFrame, mask: pd.Series
) -> None:
    scored = {name: score(p, y_col, level_col, preds[name], mask) for name in preds.columns}
    header = (
        f"  {'method':<14} {'n':>4} {'mm_MAE':>7} {'mm_RMSE':>8} {'lvl_MAE':>8} "
        f"{'bias':>7} {'dir%':>6}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, s in sorted(scored.items(), key=lambda kv: kv[1]["mm_RMSE"]):
        print(
            f"  {name:<14} {s['n']:>4.0f} {s['mm_MAE']:>7.2f} {s['mm_RMSE']:>8.2f} "
            f"{s['lvl_MAE']:>8.1f} {s['bias']:>+7.2f} {s['dir%']:>6.1f}"
        )


def run() -> None:
    print("Pulling Census/NAHB/Freddie/NOAA (all public files), cached after first run...")
    inputs = data.pull_panel(cache=CACHE)
    p = build_features(inputs)
    print(
        f"Panel: starts n={p['y_st'].notna().sum()}, permits n={p['y_pm'].notna().sum()}, "
        f"HMI n={inputs['hmi'].notna().sum()}, {p.index.min().date()}..{p.index.max().date()}"
    )

    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)
    mask_2017 = mask & (p.index >= pd.Timestamp("2017-01-01"))

    boards = {
        "STARTS": run_board(p, "y_st", "st_total", SPECS_STARTS, LGBM_STARTS, bottom_up=True),
        "PERMITS": run_board(p, "y_pm", "pm_total", SPECS_PERMITS, LGBM_PERMITS),
    }
    cols = {"STARTS": ("y_st", "st_total"), "PERMITS": ("y_pm", "pm_total")}
    for label, window_mask in (("2010+", mask), ("2017+ subwindow", mask_2017)):
        for name, preds in boards.items():
            y_col, level_col = cols[name]
            print(f"\n=== {name} m/m, % (log-points x100) [{label}] ===\n")
            _print_board(p, y_col, level_col, preds, window_mask)

    print("\nLIVE NOWCASTS (production specs)")
    for fn in (forecast_starts, forecast_permits):
        live = fn(inputs)
        if live is None:
            print(f"  {fn.__name__}: none (inputs incomplete)")
        else:
            print(
                f"  {live.target} {live.target_month.date()}: {live.level:.0f}k SAAR "
                f"({live.mm_pct:+.1f}% m/m, n_train={live.n_train})"
            )


if __name__ == "__main__":
    run()
