r"""Walk-forward bake-off: next-month BLS average egg price ($/dozen, NSA).

Target: the about-to-be-released month M print of APU0000708111, forecast just
before the mid-(M+1) CPI release. Modelled as Delta-log(AP); levels recovered as
AP_{M-1} * exp(yhat). All specs are walk-forward with an expanding window and
use only information published at the origin (AP through M-1, PPI through M-1
-- see data.py for the release-calendar argument).

Method candidates (from the literature review, 2026-06):

  * Baselines: random walk, expanding seasonal m/m, AR(1).
  * Wholesale pass-through (the workhorse in the egg literature -- retail
    follows wholesale with a 2-5 week lag): distributed lag of PPI chicken-egg
    changes, with/without an AR term.
  * ECM: adds the log(retail/wholesale) gap -- retail and wholesale egg prices
    are cointegrated and the margin mean-reverts after spikes.
  * Asymmetric pass-through ("rockets and feathers"): separate up/down
    wholesale coefficients -- retail follows increases faster than decreases.
  * SARIMA (the USDA FPO TB-1957 approach is pure time-series) and a LightGBM
    challenger (the ML literature: Zhao 2025 AEPP; ARIMAX beat LSTM in
    Q Open 2025, so expectations for ML at monthly frequency are low).

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests are against latest-vintage data (AP/PPI revisions are minor).

Run: .\.venv\Scripts\python.exe -m forecasts.bls_cpi.eggs.harness
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from forecasts.bls_cpi.eggs import data
from forecasts.bls_cpi.eggs.model import MIN_TRAIN, build_features, forecast_next

CACHE = "_eggs_panel.csv"  # scratch cache; delete to refetch
TEST_START = pd.Timestamp("2010-01-01")
COVID = (pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01"))


# --------------------------------------------------------------------------- #
# Walk-forward engines
# --------------------------------------------------------------------------- #
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


def wf_sarima(p: pd.DataFrame, order, seasonal_order, refit_every: int = 12) -> pd.Series:
    """Walk-forward SARIMA on log AP; parameters refit every `refit_every`
    origins (monthly refits add nothing and 400 MLE fits are slow)."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    logap = np.log(p["ap"])
    out = pd.Series(np.nan, index=p.index, dtype=float)
    fitted_params = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for j, i in enumerate(range(MIN_TRAIN, len(p))):
            train = logap.iloc[:i].dropna()
            if len(train) < MIN_TRAIN or pd.isna(logap.iloc[i - 1]):
                continue
            model = SARIMAX(
                train.to_numpy(),
                order=order,
                seasonal_order=seasonal_order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            if fitted_params is None or j % refit_every == 0:
                res = model.fit(disp=False, maxiter=200)
                fitted_params = res.params
            else:
                res = model.filter(fitted_params)
            out.iloc[i] = float(res.forecast(1)[0]) - float(train.iloc[-1])  # Delta-log
    return out


def wf_lgbm(p: pd.DataFrame, cols: list[str], refit_every: int = 12) -> pd.Series:
    import lightgbm as lgb

    feats = p[cols].copy()
    feats["month"] = p.index.month
    X = feats.to_numpy()  # plain ndarray end-to-end (avoids feature-name warnings)
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


# --------------------------------------------------------------------------- #
# Bake-off
# --------------------------------------------------------------------------- #
SPECS_OLS: dict[str, list[str]] = {
    "ar1": ["dap_1"],
    "seasonal_ar": ["dap_1", "seas"],
    "ppi_dl": ["dppi_1", "dppi_2"],
    "ppi_dl_ar": ["dap_1", "dppi_1", "dppi_2"],
    "ppi_dl3_ar": ["dap_1", "dppi_1", "dppi_2", "dppi_3"],
    "ecm": ["dap_1", "dppi_1", "gap_1"],
    "ecm_dl": ["dap_1", "dppi_1", "dppi_2", "gap_1"],
    "ppi_asym": ["dap_1", "dppi_1_pos", "dppi_1_neg"],
    "ecm_asym": ["dap_1", "dppi_1_pos", "dppi_1_neg", "dppi_2_pos", "dppi_2_neg", "gap_1"],
    "ecm_seas": ["dap_1", "dppi_1", "dppi_2", "gap_1", "seas"],
    "ecm_dl3": ["dap_1", "dppi_1", "dppi_2", "dppi_3", "gap_1"],
    "ppi_dl3_ar_seas": ["dap_1", "dppi_1", "dppi_2", "dppi_3", "seas"],
}


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["rw"] = 0.0
    preds["seasonal"] = p["seas"]
    for name, cols in SPECS_OLS.items():
        preds[name] = wf_ols(p, cols)
    print("  (OLS specs done; fitting SARIMA...)")
    preds["sarima"] = wf_sarima(p, order=(1, 1, 1), seasonal_order=(0, 1, 1, 12))
    print("  (SARIMA done; fitting LightGBM...)")
    preds["lgbm"] = wf_lgbm(p, ["dap_1", "dppi_1", "dppi_2", "dppi_3", "gap_1", "seas"])
    # Equal-weight combo of the strongest distinct shapes (pure DL vs ECM).
    preds["combo"] = preds[["ppi_dl3_ar", "ecm_dl3"]].mean(axis=1)
    # rw has no fit requirement -- blank it where the OLS specs are also blank
    # so every method is scored on identical months.
    ref = preds[list(SPECS_OLS)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p["y"], "yhat": yhat, "ap_lag": p["ap"].shift(1)})
    d = d[mask].dropna()
    mm_err = (d["yhat"] - d["y"]) * 100  # log-points ~ pct m/m
    lvl_err = d["ap_lag"] * np.exp(d["yhat"]) - d["ap_lag"] * np.exp(d["y"])
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
    print("Pulling BLS series (cached after first run)...")
    panel = data.pull_panel(api_key=data.maybe_api_key(), cache=CACHE)
    p = build_features(panel)
    print(
        f"Panel {panel.index.min().date()}..{panel.index.max().date()} "
        f"(AP n={panel['ap'].notna().sum()}, PPI n={panel['ppi'].notna().sum()})"
    )

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)

    scored = {name: score(p, preds[name], mask) for name in preds.columns}
    test_months = preds[mask].dropna(how="all").index
    print(
        f"\nTest window {test_months.min().date()}..{test_months.max().date()}, "
        f"COVID-masked. m/m errors in pct (log-points x100), levels in $/dozen.\n"
    )
    header = (
        f"  {'method':<14} {'n':>4} {'mm_MAE':>7} {'mm_RMSE':>8} "
        f"{'lvl_MAE':>8} {'lvl_RMSE':>9} {'bias':>7} {'dir%':>6}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, s in sorted(scored.items(), key=lambda kv: kv[1]["mm_RMSE"]):
        print(
            f"  {name:<14} {s['n']:>4.0f} {s['mm_MAE']:>7.2f} {s['mm_RMSE']:>8.2f} "
            f"{s['lvl_MAE']:>8.3f} {s['lvl_RMSE']:>9.3f} {s['bias']:>+7.2f} {s['dir%']:>6.1f}"
        )

    # Sub-window: the HPAI era (2015+) where the spikes live.
    hpai_mask = mask & (p.index >= pd.Timestamp("2015-01-01"))
    print("\n  HPAI era only (2015+, COVID-masked):")
    scored_h = {name: score(p, preds[name], hpai_mask) for name in preds.columns}
    for name, s in sorted(scored_h.items(), key=lambda kv: kv[1]["mm_RMSE"])[:6]:
        print(
            f"  {name:<14} {s['n']:>4.0f} {s['mm_MAE']:>7.2f} {s['mm_RMSE']:>8.2f} "
            f"{s['lvl_MAE']:>8.3f} {s['lvl_RMSE']:>9.3f} {s['bias']:>+7.2f} {s['dir%']:>6.1f}"
        )

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec ppi_dl3_ar_seas)")
    if live is None:
        print("  none (target-month regressors incomplete).")
    else:
        print(
            f"  {live.target_month.date()}: ${live.level:.3f}/dozen "
            f"({live.mm_pct:+.1f}% m/m, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
