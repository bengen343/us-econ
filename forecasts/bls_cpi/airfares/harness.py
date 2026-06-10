r"""Walk-forward bake-off: next-month CPI airline fares index (CUSR0000SETG01, SA).

Target: the about-to-be-released month M print, forecast just before the
mid-(M+1) CPI release. Modelled as Delta-log(SA index); levels recovered as
idx_{M-1} * exp(yhat).

Method candidates (literature review 2026-06):

  * Persistence + seasonality baselines (SA target, so the seasonal term is
    expected weak; airfares m/m is volatile and mean-reverting).
  * Producer-side fares: PPI scheduled passenger air transportation (industry
    PCU481111481111 / commodity WPU3022) at lags 1-2 -- a monthly fare
    measure published BEFORE the CPI print it parallels.
  * Fuel costs: Gulf Coast jet fuel (FRED weekly) and WTI (BigQuery daily) as
    complete-month means, lags 0-3 + trailing -- the literature puts fares'
    fuel pass-through at 1-4 QUARTERS (IATA; academic work), so short lags
    are expected weak but cheap to test. WTI is tested alongside jet fuel
    because it is already collected (a jet-fuel collector would be new infra).

COVID months (2020-03..2021-06) are masked from scoring per repo convention
(airfares collapsed ~30% in spring 2020). Backtests are against the latest
vintage; SA airfares are re-seasonalised ~annually (mild optimism vs the true
first print, same caveat as the other CPI harnesses).

Run: .\.venv\Scripts\python.exe -m forecasts.bls_cpi.airfares.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bls_cpi.airfares import data
from forecasts.bls_cpi.airfares.model import MIN_TRAIN, build_features, forecast_next

CACHE = "_air_panel.csv"  # scratch cache; delete to refetch
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
    "ar1": ["dap_1"],
    "ar2": ["dap_1", "dap_2"],
    "seasonal_ar": ["dap_1", "seas"],
    "ppi_ind": ["dap_1", "dppii_1"],
    "ppi_ind_dl": ["dap_1", "dppii_1", "dppii_2"],
    "ppi_com": ["dap_1", "dppic_1"],
    "ppi_com_dl": ["dap_1", "dppic_1", "dppic_2"],
    "jet_now": ["dap_1", "djet_0"],
    "jet_lags": ["dap_1", "djet_1", "djet_2", "djet_3"],
    "jet_trail": ["dap_1", "jet_trail6"],
    "wti_lags": ["dap_1", "dwti_1", "dwti_2"],
    "wti_trail": ["dap_1", "wti_trail6"],
    "ppi_jet": ["dap_1", "dppii_1", "djet_1", "jet_trail6"],
}


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["rw_mm"] = p["dap_1"]
    preds["zero"] = 0.0
    preds["seasonal"] = p["seas"]
    for name, cols in SPECS_OLS.items():
        preds[name] = wf_ols(p, cols)
    # Score every method on identical months (the PPI-industry series is the
    # binding history constraint).
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
    print("Pulling BLS (API) + jet fuel (FRED) + WTI (BigQuery), cached after first run...")
    panel = data.pull_panel(api_key=data.maybe_api_key(), cache=CACHE)
    p = build_features(panel)
    print(
        f"Panel {panel.index.min().date()}..{panel.index.max().date()} "
        f"(CPI n={panel['sa_idx'].notna().sum()}, PPI-ind n={panel['ppi_ind'].notna().sum()}, "
        f"jet n={panel['jet'].notna().sum()}, wti n={panel['wti'].notna().sum()})"
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
