r"""Walk-forward bake-off: core PCE m/m (BEA Personal Income release).

Target: Delta-log of the PCE price index excluding food and energy (DPCCRG)
for report month M, forecast after the month-M CPI + PPI prints (mid-M+1)
and before the PCE release (~25th-31st of M+1). Scored in m/m pp; the print
is watched to 2 decimals and the full accounting translation (Employ
America's Core-Cast) averages ~2bp absolute error -- the realistic bar for
a parsimonious regression is somewhat above that.

Method candidates (literature review 2026-06): core CPI m/m (the
translation backbone -- most PCE components are CPI-deflated); the PPI
add-ons analysts watch on PPI day (portfolio management, physician offices,
hospitals, airfares -- PCE sources these outside the CPI); the S&P 500
(portfolio-management fee proxy); own history; LightGBM challenger.

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests use the latest vintage; PCE is revised at each subsequent release
and re-benchmarked annually (a bigger caveat than for CPI -- noted in the
README).

Run: .\.venv\Scripts\python.exe -m forecasts.bea_pce.core_mm.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bea_pce.core_mm import data
from forecasts.bea_pce.core_mm.model import MIN_TRAIN, build_features, forecast_next

CACHE = "_pce_panel.csv"  # scratch cache; delete to refetch
TEST_START = pd.Timestamp("2012-01-01")  # PPI industry detail binds (~2004 starts)
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


# Portfolio-management PPI only starts 2022 (NAICS recode killed the old
# series) -- the S&P 500 proxies that channel over the full history.
SPECS_OLS: dict[str, list[str]] = {
    "ar1": ["dpce_1"],
    "ar2": ["dpce_1", "dpce_2"],
    "ccpi": ["dccpi_0"],
    "ccpi_ar": ["dccpi_0", "dpce_1"],
    "ccpi_sp": ["dccpi_0", "dsp_0", "dsp_1"],
    "ccpi_air": ["dccpi_0", "dair_0"],
    "ccpi_med": ["dccpi_0", "dphy_0", "dhos_0"],
    "ccpi_sp_air": ["dccpi_0", "dsp_0", "dair_0"],
    "ccpi_sp_air_ar": ["dccpi_0", "dsp_0", "dair_0", "dpce_1"],
    "ccpi_sp_air_med": ["dccpi_0", "dsp_0", "dair_0", "dphy_0", "dhos_0"],
    "translation": ["dccpi_0", "dsp_0", "dsp_1", "dair_0", "dphy_0", "dhos_0", "dpce_1"],
    "trans_seas": ["dccpi_0", "dsp_0", "dair_0", "dphy_0", "dhos_0", "dpce_1", "seas"],
    "kitchen": [
        "dccpi_0",
        "dccpi_1",
        "dair_0",
        "dphy_0",
        "dhos_0",
        "dsp_0",
        "dsp_1",
        "dpce_1",
        "dpce_2",
        "seas",
    ],
}
LGBM_COLS = [
    "dccpi_0",
    "dccpi_1",
    "dair_0",
    "dphy_0",
    "dhos_0",
    "dsp_0",
    "dsp_1",
    "dpce_1",
    "dpce_2",
    "seas",
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
    d = pd.DataFrame({"y": p["y"], "yhat": yhat})[mask].dropna()
    err = (d["yhat"] - d["y"]) * 100
    hit = np.sign(d["yhat"] - d["y"].median()) == np.sign(d["y"] - d["y"].median())
    return {
        "n": len(d),
        "MAE": float(err.abs().mean()),
        "RMSE": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "dir%": float(hit.mean() * 100),
    }


def run() -> None:
    print("Pulling BEA (API) + BLS (API) + S&P (BigQuery), cached after first run...")
    panel = data.pull_panel(cache=CACHE)
    p = build_features(panel)
    pce = panel["core_pce"]
    print(
        f"Panel: core PCE n={p['y'].notna().sum()} "
        f"({pce.first_valid_index().date()}..{pce.last_valid_index().date()}), "
        f"core CPI n={panel['core_cpi'].notna().sum()}, "
        f"PPI portfolio n={panel['ppi_portfolio'].notna().sum()}"
    )

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)
    mask_2017 = mask & (p.index >= pd.Timestamp("2017-01-01"))

    for label, window_mask in (("2012+", mask), ("2017+ subwindow", mask_2017)):
        scored = {name: score(p, preds[name], window_mask) for name in preds.columns}
        print(f"\n=== CORE PCE m/m, pp (log-points x100) [{label}] ===\n")
        header = f"  {'method':<16} {'n':>4} {'MAE':>6} {'RMSE':>7} {'bias':>7} {'dir%':>6}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for name, s in sorted(scored.items(), key=lambda kv: kv[1]["RMSE"]):
            print(
                f"  {name:<16} {s['n']:>4.0f} {s['MAE']:>6.3f} {s['RMSE']:>7.3f} "
                f"{s['bias']:>+7.3f} {s['dir%']:>6.1f}"
            )

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec)")
    if live is None:
        print("  none (target-month regressors incomplete)")
    else:
        print(
            f"  {live.target_month.date()}: index {live.level:.3f} "
            f"({live.mm_pct:+.3f}% m/m, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
