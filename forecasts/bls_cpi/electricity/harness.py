r"""Walk-forward bake-off: next-month BLS average electricity price ($/kWh, NSA).

Target: the about-to-be-released month M print of APU000072610, forecast just
before the mid-(M+1) CPI release. Modelled as Delta-log(AP); levels recovered
as AP_{M-1} * exp(yhat).

Method candidates (literature review 2026-06):

  * Persistence + seasonality: retail electricity is an administered price
    with strong NSA seasonality (summer rate schedules) -- the expanding
    calendar-month mean is the structural baseline.
  * Producer-side pass-through: PPI electric power (residential WPU0541 /
    all-sector WPU054) at lags 1-3, eggs-style.
  * Fuel costs: Henry Hub natural gas at lags 0-12 and as a trailing trend --
    expected weak at h=1 (rate-case delays gate the pass-through; EIA, RFF;
    structurally diverged from gas since ~2023 per FRED Blog) but tested.

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests are against the latest vintage (AP/PPI revisions are minor).

Run: .\.venv\Scripts\python.exe -m forecasts.bls_cpi.electricity.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bls_cpi.electricity import data
from forecasts.bls_cpi.electricity.model import MIN_TRAIN, build_features, forecast_next

CACHE = "_elec_panel.csv"  # scratch cache; delete to refetch
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
    "seasonal_ar": ["dap_1", "seas"],
    "seasonal_ar12": ["dap_1", "dap_12", "seas"],
    "ppi_res": ["dap_1", "seas", "dppir_1"],
    "ppi_res_dl": ["dap_1", "seas", "dppir_1", "dppir_2", "dppir_3"],
    "ppi_all_dl": ["dap_1", "seas", "dppia_1", "dppia_2", "dppia_3"],
    "ppi_res_trail": ["dap_1", "seas", "ppir_trail12"],
    "hh_now": ["dap_1", "seas", "dhh_0"],
    "hh_lags": ["dap_1", "seas", "dhh_1", "dhh_6", "dhh_12"],
    "hh_trail": ["dap_1", "seas", "hh_trail12"],
    "ppi_hh": ["dap_1", "seas", "dppir_1", "hh_trail12"],
}


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["rw"] = 0.0
    preds["seasonal"] = p["seas"]
    for name, cols in SPECS_OLS.items():
        preds[name] = wf_ols(p, cols)
    # Score every method on identical months (HH starts 1997, PPI 1980; the
    # binding constraint is the HH-lag specs' first trainable origin).
    ref = preds[list(SPECS_OLS)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p["y"], "yhat": yhat, "ap_lag": p["ap"].shift(1)})
    d = d[mask].dropna()
    mm_err = (d["yhat"] - d["y"]) * 100  # log-points ~ pct m/m
    lvl_err = (d["ap_lag"] * np.exp(d["yhat"]) - d["ap_lag"] * np.exp(d["y"])) * 100  # cents
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
    print("Pulling BLS series (API) + Henry Hub (FRED), cached after first run...")
    panel = data.pull_panel(api_key=data.maybe_api_key(), cache=CACHE)
    p = build_features(panel)
    print(
        f"Panel {panel.index.min().date()}..{panel.index.max().date()} "
        f"(AP n={panel['ap'].notna().sum()}, PPI-res n={panel['ppi_res'].notna().sum()}, "
        f"HH n={panel['hh'].notna().sum()})"
    )

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)

    scored = {name: score(p, preds[name], mask) for name in preds.columns}
    test_months = preds[mask].dropna(how="all").index
    print(
        f"\nTest window {test_months.min().date()}..{test_months.max().date()}, "
        f"COVID-masked. m/m errors in pct (log-points x100), levels in cents/kWh.\n"
    )
    header = (
        f"  {'method':<16} {'n':>4} {'mm_MAE':>7} {'mm_RMSE':>8} "
        f"{'lvl_MAE':>8} {'lvl_RMSE':>9} {'bias':>7} {'dir%':>6}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, s in sorted(scored.items(), key=lambda kv: kv[1]["mm_RMSE"]):
        print(
            f"  {name:<16} {s['n']:>4.0f} {s['mm_MAE']:>7.3f} {s['mm_RMSE']:>8.3f} "
            f"{s['lvl_MAE']:>8.3f} {s['lvl_RMSE']:>9.3f} {s['bias']:>+7.3f} {s['dir%']:>6.1f}"
        )

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec)")
    if live is None:
        print("  none (target-month regressors incomplete).")
    else:
        print(
            f"  {live.target_month.date()}: ${live.level:.3f}/kWh "
            f"({live.mm_pct:+.2f}% m/m, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
