"""Shared model code for the Challenger job-cuts headline forecast.

The target is modeled in logs (the level is right-skewed, ~15k-670k). The
bake-off winner (``harness.py``) is an ensemble, averaged in log space, of two
ridge regressions on month dummies + the prior-month log headline:

  * ``ism``    — + ISM-manufacturing-employment deviation (50 - index)   [λ=5]
  * ``allind`` — ``ism`` + initial-claims YoY + Conference-Board labor
                 differential + Michigan sentiment                       [λ=15]

All predictors are observable before the report's ~first-Thursday release of
month M+1. ISM employment is the tightest-timed input (it lands ~1 day before
Challenger), so when any month-M indicator beyond the prior headline is missing
the model falls back to the seasonal-dummy + AR(1) base (``seas_dummies_ar1``),
which still beats a random walk by ~11%. ``forecast_next`` averages whichever of
the two ensemble members are computable, else the base.

Common-test-set bake-off (2016-2026, COVID masked): ensemble RMSE 31.8k
(-11% vs random walk, -42% vs seasonal-naive), MdAE 9.2k, direction 68%.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MONTH_DUMMIES = [f"m_{i}" for i in range(2, 13)]
BASE_COLS = ["ly_l1"] + MONTH_DUMMIES
SPEC_ISM_COLS = BASE_COLS + ["ism_dev"]
SPEC_ALLIND_COLS = BASE_COLS + ["ism_dev", "claims_yoy", "cb_labor_differential", "mich"]

LAMBDA_ISM = 5.0
LAMBDA_ALLIND = 15.0
MIN_TRAIN = 48


def build_features(panel: pd.DataFrame, extra_months: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    """Engineer the monthly design matrix from the raw panel (target + indicators).

    ``extra_months`` extends the index so the (as-yet-unobserved) target month's
    feature row — built from its prior-month lag and same-month indicators — is
    present for prediction.
    """
    index = panel.index
    if extra_months:
        index = index.union(pd.DatetimeIndex(extra_months))
    df = pd.DataFrame(index=index)
    df["y"] = panel["y"].reindex(index)
    df["ly"] = np.log(df["y"])
    df["ly_l1"] = df["ly"].shift(1)

    df["month"] = df.index.month
    for i in range(2, 13):
        df[f"m_{i}"] = (df["month"] == i).astype(float)

    # Same-month indicators (all observable before the M+1 release).
    claims_log = np.log(panel["claims_nsa"]).reindex(index)
    df["claims_yoy"] = claims_log - claims_log.shift(12)
    df["ism_dev"] = (50.0 - panel["ism_emp"]).reindex(index)       # contraction depth
    df["cb_labor_differential"] = panel["cb_labor_differential"].reindex(index)
    df["mich"] = panel["mich"].reindex(index)
    return df


def _ridge_fit_predict(feat: pd.DataFrame, target: pd.Timestamp, cols: list[str],
                       lam: float) -> tuple[float, int] | None:
    """Standardized-ridge log forecast for ``target``; None if inputs are missing."""
    if feat.loc[target, cols].isna().any():
        return None
    i = feat.index.get_loc(target)
    sub = feat.iloc[:i].dropna(subset=["ly"] + cols)
    if len(sub) < MIN_TRAIN:
        return None
    X = sub[cols].to_numpy(float)
    y = sub["ly"].to_numpy(float)
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Xs = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    xs = np.concatenate([[1.0], (feat.loc[target, cols].to_numpy(float) - mu) / sd])
    p = Xs.shape[1]
    R = lam * np.eye(p)
    R[0, 0] = 0.0
    beta = np.linalg.solve(Xs.T @ Xs + R, Xs.T @ y)
    return float(xs @ beta), len(sub)


@dataclass(frozen=True)
class ChallengerForecast:
    target_month: pd.Timestamp
    level: float          # announced job cuts (persons)
    change: float         # vs the prior month's headline
    n_train: int
    method: str           # 'ensemble', 'ism_only', 'allind_only', or 'fallback'


def forecast_next(panel: pd.DataFrame) -> ChallengerForecast | None:
    """Fit on all published history and nowcast the next (unreleased) month.

    Averages, in log space, whichever ensemble members are computable from the
    month's available indicators; falls back to the seasonal-dummy + AR(1) base
    when only the prior headline is known. Returns None only when there is no
    usable history (no prior headline)."""
    last = panel["y"].last_valid_index()
    if last is None:
        return None
    target = last + pd.offsets.MonthBegin(1)
    feat = build_features(panel, extra_months=[target])

    if pd.isna(feat.loc[target, "ly_l1"]):
        return None  # prior-month headline not yet in BigQuery

    members = []
    ism = _ridge_fit_predict(feat, target, SPEC_ISM_COLS, LAMBDA_ISM)
    allind = _ridge_fit_predict(feat, target, SPEC_ALLIND_COLS, LAMBDA_ALLIND)
    for r in (ism, allind):
        if r is not None:
            members.append(r)

    if len(members) == 2:
        method = "ensemble"
    elif len(members) == 1:
        method = "ism_only" if ism is not None else "allind_only"
    else:
        base = _ridge_fit_predict(feat, target, BASE_COLS, lam=1e-6)  # ~OLS seas+AR1 base
        if base is None:
            return None
        members = [base]
        method = "fallback"

    log_pred = float(np.mean([m[0] for m in members]))
    n_train = int(min(m[1] for m in members))
    level = float(np.exp(log_pred))
    anchor = float(panel.at[last, "y"])
    return ChallengerForecast(
        target_month=target,
        level=level,
        change=level - anchor,
        n_train=n_train,
        method=method,
    )
