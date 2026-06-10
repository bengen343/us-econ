r"""Walk-forward bake-offs: Michigan ICS preliminary + final (revision).

Two targets, scored in index points:

  * ``y_p`` = prelim_M - final_{M-1} (the hard one -- consensus misses by
    1.5-2.5 points in volatile regimes).
  * ``y_r`` = final_M - prelim_M (the revision; prelim<->final correlation is
    ~0.97, so the bar is the zero-revision baseline).

Method candidates (literature review 2026-06):

  * Persistence/momentum/seasonal baselines.
  * Gasoline prices -- the dominant 2025-26 sentiment driver (Strait of
    Hormuz): daily Gulf Coast spot + weekly retail, in early-month windows
    for the prelim and post-prelim-window changes for the revision.
  * S&P 500 (same windows): wealth/news channel.
  * SF Fed Daily News Sentiment Index (daily lexical news measure, 1980+):
    documented UMich-sentiment predictor.
  * Conference Board confidence lag 1 (cross-survey).
  * LightGBM challenger on the full feature set per repo convention.

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Michigan history itself is never revised (prelim->final IS the revision), so
latest-vintage backtesting is exact here -- no first-print caveat.

Run: .\.venv\Scripts\python.exe -m forecasts.michigan_sentiment.headline.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.michigan_sentiment.headline import data
from forecasts.michigan_sentiment.headline.model import (
    MIN_TRAIN,
    build_features,
    forecast_final,
    forecast_prelim,
)

CACHE = "_mich_panel.csv"  # scratch cache; delete to refetch
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
    """ML challenger (mirrors the eggs/PPI harnesses)."""
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


SPECS_PRELIM: dict[str, list[str]] = {
    "ar": ["dfin_1", "seas_p"],
    "gas": ["dgas_early", "dgas_m1"],
    "retail": ["dretail_early"],
    "sp": ["dsp_early", "dsp_m1"],
    "dnsi": ["ddnsi_early", "ddnsi_m1"],
    "cb": ["dcb_1"],
    "gas_sp": ["dgas_early", "dsp_early"],
    "gas_sp_ar": ["dgas_early", "dsp_early", "dfin_1"],
    "gas_sp_dnsi": ["dgas_early", "dsp_early", "ddnsi_early"],
    "gas_dnsi": ["dgas_early", "ddnsi_early"],
    "kitchen_p": ["dgas_early", "dgas_m1", "dsp_early", "ddnsi_early", "dcb_1", "dfin_1"],
    # Survey-window-aligned variants (interviews run ~25th of M-1 .. 7th of M).
    "gas_sw": ["dgas_sw"],
    "retail_sw": ["dretail_sw"],
    "sp_sw": ["dsp_sw"],
    "dnsi_sw": ["ddnsi_sw"],
    "gas_sp_sw": ["dgas_sw", "dsp_sw"],
    "gas_sp_dnsi_sw": ["dgas_sw", "dsp_sw", "ddnsi_sw"],
    "sw_kitchen": ["dgas_sw", "dretail_sw", "dsp_sw", "ddnsi_sw", "dcb_1", "dfin_1"],
}
LGBM_PRELIM = [
    "dgas_early",
    "dgas_m1",
    "dretail_early",
    "dsp_early",
    "dsp_m1",
    "ddnsi_early",
    "ddnsi_m1",
    "dcb_1",
    "dfin_1",
    "rev_1",
]

SPECS_FINAL: dict[str, list[str]] = {
    "ar_r": ["rev_1", "seas_r"],
    "prelim_move": ["y_p"],
    "gas_late": ["dgas_late"],
    "retail_late": ["dretail_late"],
    "sp_late": ["dsp_late"],
    "dnsi_late": ["ddnsi_late"],
    "gas_sp_late": ["dgas_late", "dsp_late"],
    "gas_sp_dnsi_late": ["dgas_late", "dsp_late", "ddnsi_late"],
    "kitchen_r": ["dgas_late", "dretail_late", "dsp_late", "ddnsi_late", "y_p", "rev_1"],
    # Post-prelim-window variants (final's extra interviews vs the prelim window).
    "gas_post": ["dgas_sw_post"],
    "retail_post": ["dretail_sw_post"],
    "sp_post": ["dsp_sw_post"],
    "dnsi_post": ["ddnsi_sw_post"],
    "gas_sp_post": ["dgas_sw_post", "dsp_sw_post"],
    "post_kitchen": ["dgas_sw_post", "dretail_sw_post", "dsp_sw_post", "ddnsi_sw_post", "y_p"],
}
LGBM_FINAL = ["dgas_late", "dretail_late", "dsp_late", "ddnsi_late", "y_p", "rev_1", "dfin_1"]


def run_bakeoff(
    p: pd.DataFrame, y_col: str, specs: dict[str, list[str]], lgbm_cols: list[str]
) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["zero"] = 0.0
    if y_col == "y_p":
        preds["mom"] = p["dfin_1"]  # extrapolate the last final-to-final move
        preds["carry_prelim"] = -p["rev_1"]  # prelim_M = prelim_{M-1}
        preds["seasonal"] = p["seas_p"]
    else:
        preds["ar_naive"] = p["rev_1"]
        preds["seasonal"] = p["seas_r"]
    for name, cols in specs.items():
        preds[name] = wf_ols(p, y_col, cols)
    preds["lgbm"] = wf_lgbm(p, y_col, lgbm_cols)
    ref = preds[list(specs)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(p: pd.DataFrame, y_col: str, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p[y_col], "yhat": yhat})[mask].dropna()
    err = d["yhat"] - d["y"]
    hit = np.sign(d["yhat"]) == np.sign(d["y"])
    return {
        "n": len(d),
        "MAE": float(err.abs().mean()),
        "RMSE": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "dir%": float(hit.mean() * 100),
    }


def _print_board(p: pd.DataFrame, y_col: str, preds: pd.DataFrame, mask: pd.Series) -> None:
    scored = {name: score(p, y_col, preds[name], mask) for name in preds.columns}
    header = f"  {'method':<18} {'n':>4} {'MAE':>6} {'RMSE':>7} {'bias':>7} {'dir%':>6}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, s in sorted(scored.items(), key=lambda kv: kv[1]["RMSE"]):
        print(
            f"  {name:<18} {s['n']:>4.0f} {s['MAE']:>6.2f} {s['RMSE']:>7.2f} "
            f"{s['bias']:>+7.2f} {s['dir%']:>6.1f}"
        )


def run() -> None:
    print("Pulling Michigan/CB/EIA (BigQuery) + Yahoo + SF Fed, cached after first run...")
    inputs = data.pull_panel(cache=CACHE)
    p = build_features(inputs)
    print(
        f"Panel: prelim n={p['y_p'].notna().sum()}, revision n={p['y_r'].notna().sum()}, "
        f"{p.index.min().date()}..{p.index.max().date()}"
    )

    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)
    mask_2017 = mask & (p.index >= pd.Timestamp("2017-01-01"))

    preds_p = run_bakeoff(p, "y_p", SPECS_PRELIM, LGBM_PRELIM)
    preds_r = run_bakeoff(p, "y_r", SPECS_FINAL, LGBM_FINAL)
    for label, window_mask in (("2010+", mask), ("2017+ subwindow", mask_2017)):
        print(f"\n=== PRELIMINARY (y_p = prelim_M - final_(M-1)), points [{label}] ===\n")
        _print_board(p, "y_p", preds_p, window_mask)
        print(f"\n=== FINAL / REVISION (y_r = final_M - prelim_M), points [{label}] ===\n")
        _print_board(p, "y_r", preds_r, window_mask)

    print("\nLIVE NOWCASTS (production specs)")
    for fn in (forecast_prelim, forecast_final):
        live = fn(inputs)
        if live is None:
            print(f"  {fn.__name__}: none pending")
        else:
            print(
                f"  {live.target} {live.target_month.date()}: {live.level:.1f} "
                f"({live.change:+.1f} vs anchor, n_train={live.n_train})"
            )


if __name__ == "__main__":
    run()
