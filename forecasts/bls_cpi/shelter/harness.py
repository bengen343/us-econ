r"""Walk-forward bake-off: next-month CPI shelter index (CUSR0000SAH1, SA).

Target: the about-to-be-released month M print, forecast just before the
mid-(M+1) CPI release. Modelled as Delta-log(SA index); levels recovered as
idx_{M-1} * exp(yhat).

Method candidates (literature review 2026-06):

  * Persistence: carry-forward m/m, trailing 3/6/12 means, AR specs --
    shelter's long leases + the CPI's 6-month sampling window make it the
    most persistent CPI component; this is the frontier to beat at h=1.
  * Sub-components: lag-1 OER + rent m/m (within-shelter composition shifts).
  * Market rents: ZORI lags/trends and the market-vs-CPI catch-up gap
    (Boston Fed cpp20230216 uses the 6-month ZORI lag; Richmond Fed / NBER
    w34113 put the full pass-through at 8-14 months). ZORI starts 2015, so
    ZORI specs are scored on a separate shorter window (reduced MIN_TRAIN)
    against the same non-ZORI specs.

Two scoring tables: full window (2010+, full-history specs only) and the ZORI
window (2021-07+ -- post-COVID-mask -- all specs, MIN_TRAIN=60). COVID months
(2020-03..2021-06) are masked per repo convention. Backtests are against the
latest vintage; SA shelter is re-seasonalised ~annually (mild optimism vs the
first print, same caveat as the other CPI harnesses).

Run: .\.venv\Scripts\python.exe -m forecasts.bls_cpi.shelter.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bls_cpi.shelter import data
from forecasts.bls_cpi.shelter.model import build_features, forecast_next

CACHE = "_shelter_panel.csv"  # scratch cache; delete to refetch
MIN_TRAIN = 96  # for the fitted (OLS) research specs; production is deterministic
TEST_START = pd.Timestamp("2010-01-01")
ZORI_TEST_START = pd.Timestamp("2021-07-01")
MIN_TRAIN_ZORI = 60
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


SPECS_FULL: dict[str, list[str]] = {
    "ar1": ["dsh_1"],
    "ar2": ["dsh_1", "dsh_2"],
    "ar_yr": ["dsh_1", "dsh_2", "dsh_12"],
    "ar_trail": ["dsh_1", "trail3", "trail12"],
    "components": ["dsh_1", "oer_1", "rent_1"],
    "comp_trail": ["dsh_1", "trail3", "trail12", "oer_1", "rent_1"],
    "ar_trail_seas": ["dsh_1", "trail3", "trail12", "seas"],
}

SPECS_ZORI: dict[str, list[str]] = {
    "zori_l6": ["dsh_1", "zori_6"],
    "zori_lags": ["dsh_1", "zori_1", "zori_6", "zori_12"],
    "zori_trend": ["dsh_1", "trail3", "zori_trail12"],
    "zori_gap": ["dsh_1", "trail3", "trail12", "zori_gap"],
}


def run_bakeoff(p: pd.DataFrame, min_train: int) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["rw_mm"] = p["dsh_1"]  # carry forward last m/m
    preds["trail3"] = p["trail3"]
    preds["trail6"] = p["trail6"]
    preds["trail12"] = p["trail12"]
    for name, cols in SPECS_FULL.items():
        preds[name] = wf_ols(p, cols, min_train)
    for name, cols in SPECS_ZORI.items():
        preds[name] = wf_ols(p, cols, min_train)
    return preds


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p["y"], "yhat": yhat, "idx_lag": p["sh_idx"].shift(1)})
    d = d[mask].dropna()
    mm_err = (d["yhat"] - d["y"]) * 100  # log-points ~ pct m/m
    lvl_err = d["idx_lag"] * np.exp(d["yhat"]) - d["idx_lag"] * np.exp(d["y"])
    return {
        "n": len(d),
        "mm_MAE": float(mm_err.abs().mean()),
        "mm_RMSE": float(np.sqrt((mm_err**2).mean())),
        "lvl_MAE": float(lvl_err.abs().mean()),
        "lvl_RMSE": float(np.sqrt((lvl_err**2).mean())),
        "bias": float(mm_err.mean()),
    }


def _table(p: pd.DataFrame, preds: pd.DataFrame, mask: pd.Series, names: list[str]) -> None:
    header = (
        f"  {'method':<16} {'n':>4} {'mm_MAE':>7} {'mm_RMSE':>8} "
        f"{'lvl_MAE':>8} {'lvl_RMSE':>9} {'bias':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    scored = {name: score(p, preds[name], mask) for name in names}
    for name, s in sorted(scored.items(), key=lambda kv: kv[1]["mm_RMSE"]):
        print(
            f"  {name:<16} {s['n']:>4.0f} {s['mm_MAE']:>7.3f} {s['mm_RMSE']:>8.3f} "
            f"{s['lvl_MAE']:>8.3f} {s['lvl_RMSE']:>9.3f} {s['bias']:>+7.3f}"
        )


def run() -> None:
    print("Pulling CPI (BLS API) + ZORI (BigQuery), cached after first run...")
    panel = data.pull_panel(api_key=data.maybe_api_key(), cache=CACHE)
    p = build_features(panel)
    print(
        f"Panel {panel.index.min().date()}..{panel.index.max().date()} "
        f"(shelter n={panel['sh_idx'].notna().sum()}, ZORI n={panel['zori'].notna().sum()})"
    )

    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])

    baselines = ["rw_mm", "trail3", "trail6", "trail12"]

    # Full window: full-history specs, MIN_TRAIN=96, test 2010+.
    preds = run_bakeoff(p, MIN_TRAIN)
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)
    print(f"\nFULL WINDOW (>= {TEST_START.date()}, COVID-masked); m/m in pct, level in points.\n")
    _table(p, preds, mask, baselines + list(SPECS_FULL))

    # ZORI window: everything, reduced MIN_TRAIN so ZORI specs have history.
    preds_z = run_bakeoff(p, MIN_TRAIN_ZORI)
    mask_z = pd.Series(p.index >= ZORI_TEST_START, index=p.index)
    print(f"\nZORI WINDOW (>= {ZORI_TEST_START.date()}, MIN_TRAIN={MIN_TRAIN_ZORI}).\n")
    _table(p, preds_z, mask_z, baselines + list(SPECS_FULL) + list(SPECS_ZORI))

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec: trailing-6 mean)")
    if live is None:
        print("  none (target-month regressors incomplete).")
    else:
        print(
            f"  {live.target_month.date()}: index {live.level:.3f} "
            f"({live.mm_pct:+.2f}% m/m SA, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
