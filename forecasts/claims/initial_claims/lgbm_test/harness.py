"""Shared harness for the LGBM/XGBoost SA initial-claims exploration.

Conventions (match production / phase-2 backtest):

  * Origin   = Saturday-ending week T at which we forecast.
  * Features = computable from data observable by origin T.
  * Target   = first-print SA initial claims at week T + 7 days
               (sa_actual, from fct_actuals_as_reported).
  * Eval window mirrors Phase-2: origin in [2024-07-01, last_complete_target].
  * Comparison MAE: TimesFM 2.5 ~6.8k, ens_w60 (0.6*ARIMA + 0.4*snaive) ~8.3k,
    snaive (claims[T+1] = claims[T+1 - 364d]) computed locally.

Public API:
    load_data(data_dir)               -> dict of DataFrames
    build_panel(...)                  -> (X, y, origins, feature_names)
    walk_forward_eval(...)            -> dict of summary metrics and per-fold preds
"""

from __future__ import annotations

import pathlib
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings(
    "ignore",
    message="X does not have valid feature names",
    category=UserWarning,
)


def _target_week_holiday(target_week_end: pd.Timestamp) -> str:
    """Return holiday label if target week (Sat-ending) brackets a federal holiday.
    Returns one of: 'mlk', 'presidents', 'memorial', 'labor_day', 'columbus',
    'thanksgiving', 'fixed_holiday', or '' if none.
    The target week covers (target_week_end - 6d ... target_week_end).
    """
    start = target_week_end - pd.Timedelta(days=6)
    for offset in range(7):
        day = start + pd.Timedelta(days=offset)
        if (day.month, day.day) in {(1, 1), (7, 4), (11, 11), (12, 25)}:
            return "fixed_holiday"
        if day.weekday() == 0:  # Monday
            if day.month == 5 and (day + pd.Timedelta(days=7)).month == 6:
                return "memorial"
            if day.month == 9 and day.day <= 7:
                return "labor_day"
            if day.month == 1 and 15 <= day.day <= 21:
                return "mlk"
            if day.month == 2 and 15 <= day.day <= 21:
                return "presidents"
            if day.month == 10 and 8 <= day.day <= 14:
                return "columbus"
        if day.weekday() == 3 and day.month == 11 and 22 <= day.day <= 28:
            return "thanksgiving"
    return ""

# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

REPO = pathlib.Path(__file__).resolve().parents[3]
DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"


def load_data(data_dir: pathlib.Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    """Load the parquet files into a dict of DataFrames keyed by source."""
    sa = pd.read_parquet(data_dir / "sa_claims.parquet")
    trends = pd.read_parquet(data_dir / "trends.parquet")
    warn = pd.read_parquet(data_dir / "warn_weekly.parquet")
    adp = pd.read_parquet(data_dir / "adp_weekly.parquet")
    for d in (sa, trends, warn, adp):
        d["week_ending"] = pd.to_datetime(d["week_ending"])
    return {"sa": sa, "trends": trends, "warn": warn, "adp": adp}


# ----------------------------------------------------------------------------
# Feature engineering
# ----------------------------------------------------------------------------

@dataclass
class FeatureSpec:
    """How to build features for one panel.

    target_lags    : list of integers k -> SA at (origin - 7*k). 0 = SA at origin.
                     (Note: lag 0 is the value at the origin week, which IS observed
                      by Thursday of the following week in production via the press
                      advance; so lag 0 is a legit feature.)
    diff_lags      : list of k -> SA[origin] - SA[origin - 7k] (week-over-week deltas).
    roll_means     : list of window sizes -> rolling mean of SA over last W weeks.
    seasonal       : if True, add SA at (origin - 364d) and at (origin + 7d - 364d).
    trends_cols    : list of column names from trends.parquet to include.
    trends_lags    : list of k -> column value at (origin - 7*k) for each trends col.
    warn_lags      : list of k for warn_weekly. k>0 = backward (warn at origin-7k);
                     k<0 = forward (warn at origin + 7*|k|). Forward features assume
                     warn-effective-by-origin coverage (PIT-loose; fine for first pass).
    warn_roll      : list of (window, lag) -> mean of warn over W weeks ending at
                     origin - 7*lag (positive lag = backward shifted).
    adp_cols       : list of column names from adp_weekly to include.
    adp_lags       : list of k -> level adp_col at origin-7k. Must be k>=8 (ADP
                     publication lag is ~53 days, so earlier lags reference
                     unpublished data).
    adp_diff_lags  : list of k -> week-over-week change of adp_col at origin-7k:
                     adp_col[origin-7k] - adp_col[origin-7(k+1)]. Same k>=8 floor.
    calendar       : if True, add month and isocalendar week.
    """
    target_lags: list[int] = field(default_factory=lambda: list(range(1, 9)))
    diff_lags: list[int] = field(default_factory=lambda: [1, 2, 4])
    roll_means: list[int] = field(default_factory=lambda: [4, 8])
    seasonal: bool = True
    trends_cols: list[str] = field(default_factory=list)
    trends_lags: list[int] = field(default_factory=lambda: [0, 1, 2])
    warn_lags: list[int] = field(default_factory=list)
    warn_roll: list[tuple[int, int]] = field(default_factory=list)
    adp_cols: list[str] = field(default_factory=list)
    adp_lags: list[int] = field(default_factory=list)
    adp_diff_lags: list[int] = field(default_factory=list)
    calendar: bool = True
    holidays: bool = False  # one-hot federal holiday in target week (origin + 7d)


def build_panel(
    data: dict[str, pd.DataFrame],
    spec: FeatureSpec,
    target_col: str = "sa_actual",
    input_col: str = "sa_input",
):
    """Build the modelling panel. Returns (X, y, origins, feature_names).

    Each row corresponds to one origin week T. Features all computable from data
    knowable by T (using `input_col` for past SA). Target y = data['sa'][target_col]
    at week T + 7 days.

    Rows with any NaN in features OR target are dropped from the returned arrays.
    """
    sa = data["sa"].set_index("week_ending").sort_index()
    sa_input = sa[input_col]
    sa_target = sa[target_col]

    panel = pd.DataFrame(index=sa.index)

    # SA-based features (all in terms of sa_input as known by origin T)
    for k in spec.target_lags:
        panel[f"sa_lag{k}"] = sa_input.shift(k)
    for k in spec.diff_lags:
        panel[f"sa_diff{k}"] = sa_input - sa_input.shift(k)
    for w in spec.roll_means:
        panel[f"sa_rollmean{w}"] = sa_input.rolling(window=w, min_periods=w).mean()
    if spec.seasonal:
        # SA at same calendar week last year (origin - 52 weeks)
        panel["sa_seas52"] = sa_input.shift(52)
        # Year-ago value of the TARGET week (origin + 7 - 364 days = origin - 51 weeks)
        # = sa_input.shift(51) since shifting on the origin index, target = origin+1 week.
        panel["sa_seas_target"] = sa_input.shift(51)

    # Calendar
    if spec.calendar:
        panel["month"] = panel.index.month
        panel["isoweek"] = panel.index.isocalendar().week.astype(int)

    # Holiday flags: federal holidays falling within the TARGET week (origin + 1..7d)
    if spec.holidays:
        target_week_end = panel.index + pd.Timedelta(days=7)
        for hol_name in ("mlk", "presidents", "memorial", "labor_day", "columbus",
                          "thanksgiving", "fixed_holiday"):
            panel[f"hol_{hol_name}"] = [int(_target_week_holiday(d) == hol_name)
                                          for d in target_week_end]

    # Trends
    if spec.trends_cols:
        tr = data["trends"].set_index("week_ending").sort_index()
        # Reindex to SA grid; forward-fill within reason to handle small gaps.
        tr = tr.reindex(panel.index).ffill(limit=2)
        for col in spec.trends_cols:
            if col not in tr.columns:
                raise KeyError(f"trends column {col!r} not found; have {list(tr.columns)}")
            for k in spec.trends_lags:
                panel[f"{col}_lag{k}"] = tr[col].shift(k)

    # WARN
    if spec.warn_lags or spec.warn_roll:
        warn = data["warn"].set_index("week_ending").sort_index()
        # Reindex to SA grid, fill missing weeks with 0 (no notices = 0 affected)
        warn = warn.reindex(panel.index, fill_value=0)
        warn_w = warn["warn_workers"].astype(float)
        warn_n = warn["warn_notices"].astype(float)
        for k in spec.warn_lags:
            # k>0 backward (past), k<0 forward (uses notices effective-future-of-T,
            # assumed visible by T via filed-ahead-of-effective convention).
            panel[f"warn_workers_lag{k}"] = warn_w.shift(k)
            panel[f"warn_notices_lag{k}"] = warn_n.shift(k)
        for window, lag in spec.warn_roll:
            shifted = warn_w.shift(lag)
            panel[f"warn_workers_roll{window}_lag{lag}"] = shifted.rolling(window=window, min_periods=window).mean()

    # ADP weekly (NER level / SA level). Pub-lag ~53 days => k>=8 required.
    if spec.adp_cols and (spec.adp_lags or spec.adp_diff_lags):
        adp = data["adp"].set_index("week_ending").sort_index()
        adp = adp.reindex(panel.index).ffill(limit=1)
        for col in spec.adp_cols:
            if col not in adp.columns:
                raise KeyError(f"adp column {col!r} not found; available: {list(adp.columns)[:10]}...")
            series = adp[col].astype(float)
            for k in spec.adp_lags:
                panel[f"{col}_lag{k}"] = series.shift(k)
            for k in spec.adp_diff_lags:
                panel[f"{col}_diff_lag{k}"] = series.shift(k) - series.shift(k + 1)

    # Target: y at row indexed by origin T = sa_target at week T+7.
    panel["y"] = sa_target.shift(-1)
    panel["origin"] = panel.index

    feature_cols = [c for c in panel.columns if c not in ("y", "origin")]
    return panel, feature_cols


# ----------------------------------------------------------------------------
# Walk-forward evaluation
# ----------------------------------------------------------------------------

@dataclass
class EvalSpec:
    """Eval window + training-floor configuration."""
    train_start: str = "2010-01-01"   # earliest origin used for training
    eval_start: str = "2024-07-01"    # first origin in held-out eval (matches Phase-2)
    eval_end: str | None = None        # if None, use last origin with target available
    train_mask_ranges: tuple[tuple[str, str], ...] = ()  # exclude these date ranges from training


def _snaive_pred(panel: pd.DataFrame, origin: pd.Timestamp) -> float | None:
    """Snaive prediction for target at origin+7: sa_input at (target_week - 364d)
    = sa_input at (origin + 7 - 364) = origin - 51 weeks."""
    candidate = origin - pd.Timedelta(days=357)  # 51*7
    if candidate in panel.index:
        v = panel.loc[candidate, "sa_lag0"] if "sa_lag0" in panel.columns else None
        # fall back: panel doesn't always have sa_lag0; use seas_target
        if "sa_seas_target" in panel.columns:
            return panel.loc[origin, "sa_seas_target"] if origin in panel.index else None
    return panel.loc[origin, "sa_seas_target"] if origin in panel.index else None


def walk_forward_eval(
    panel: pd.DataFrame,
    feature_cols: list[str],
    eval: EvalSpec,
    model: str = "lgbm",
    lgbm_params: dict | None = None,
    xgb_params: dict | None = None,
    refit_every: int = 4,
    target_mode: str = "level",  # 'level' or 'residual_snaive'
) -> dict[str, Any]:
    """Walk-forward h=1 evaluation. Returns dict with overall metrics and per-fold
    predictions DataFrame.

    refit_every: refit the model every K origins (1 = retrain every week — slow;
    4 = monthly retrain is a reasonable speed/accuracy tradeoff for LGBM).
    """
    # Drop rows missing any feature or target.
    valid = panel.dropna(subset=feature_cols + ["y"]).copy()
    valid = valid[valid["origin"] >= pd.Timestamp(eval.train_start)]

    eval_start = pd.Timestamp(eval.eval_start)
    if eval.eval_end is None:
        eval_end = valid["origin"].max()
    else:
        eval_end = pd.Timestamp(eval.eval_end)

    test_idx = valid.index[(valid["origin"] >= eval_start) & (valid["origin"] <= eval_end)]
    if len(test_idx) == 0:
        raise RuntimeError(f"no eval rows in [{eval_start.date()}, {eval_end.date()}]")

    preds = []
    last_fit_idx = -1
    fitted_model = None

    if target_mode == "residual_snaive" and "sa_seas_target" not in valid.columns:
        raise RuntimeError("target_mode='residual_snaive' requires sa_seas_target in panel; set FeatureSpec.seasonal=True")

    for i, t in enumerate(test_idx):
        train_mask = valid.index < t
        for mr in eval.train_mask_ranges:
            lo, hi = pd.Timestamp(mr[0]), pd.Timestamp(mr[1])
            train_mask = train_mask & ~((valid.index >= lo) & (valid.index <= hi))
        Xtr = valid.loc[train_mask, feature_cols].values
        if target_mode == "level":
            ytr = valid.loc[train_mask, "y"].values
        elif target_mode == "residual_snaive":
            ytr = (valid.loc[train_mask, "y"] - valid.loc[train_mask, "sa_seas_target"]).values
        else:
            raise ValueError(f"unknown target_mode {target_mode!r}")
        Xte = valid.loc[[t], feature_cols].values
        yte = valid.loc[t, "y"]

        if fitted_model is None or i - last_fit_idx >= refit_every:
            fitted_model = _fit(model, Xtr, ytr, lgbm_params, xgb_params)
            last_fit_idx = i

        raw_pred = float(fitted_model.predict(Xte)[0])
        snaive = float(valid.loc[t, "sa_seas_target"]) if "sa_seas_target" in valid.columns else float("nan")
        if target_mode == "level":
            yhat = raw_pred
        else:
            yhat = snaive + raw_pred
        preds.append({
            "origin": valid.loc[t, "origin"],
            "y_true": float(yte),
            "y_pred": yhat,
            "snaive": snaive,
        })

    pred_df = pd.DataFrame(preds)
    out = {
        "n": len(pred_df),
        "model_mae": float(np.mean(np.abs(pred_df["y_pred"] - pred_df["y_true"]))),
        "model_rmse": float(np.sqrt(np.mean((pred_df["y_pred"] - pred_df["y_true"]) ** 2))),
        "model_bias": float(np.mean(pred_df["y_pred"] - pred_df["y_true"])),
        "snaive_mae": float(np.mean(np.abs(pred_df["snaive"] - pred_df["y_true"]))),
        "snaive_rmse": float(np.sqrt(np.mean((pred_df["snaive"] - pred_df["y_true"]) ** 2))),
        "eval_start": eval_start.date().isoformat(),
        "eval_end": eval_end.date().isoformat(),
        "preds": pred_df,
    }
    return out


def walk_forward_classify(
    panel: pd.DataFrame,
    feature_cols: list[str],
    eval: EvalSpec,
    lgbm_params: dict | None = None,
    refit_every: int = 4,
    input_col: str = "sa_input",
) -> dict[str, Any]:
    """Walk-forward binary classification of direction.

    Label at row T = 1 if y(T) > sa_input(T), else 0. (Flat is collapsed to 0;
    only ~4% of weeks in our eval are exactly flat at the headline-rounded level.)
    Returns dict with per-origin probabilities P(up), predicted direction, and
    diagnostic metrics including Brier score and ECE.
    """
    import lightgbm as lgb

    valid = panel.dropna(subset=feature_cols + ["y"]).copy()
    valid = valid[valid["origin"] >= pd.Timestamp(eval.train_start)]
    if input_col not in valid.columns:
        # fall back to sa_lag1 (which equals input at origin if target lags include 0)
        if "sa_lag0" in valid.columns:
            this_week_col = "sa_lag0"
        else:
            raise RuntimeError(f"need {input_col} or sa_lag0 in panel; have {list(valid.columns)[:10]}")
    else:
        this_week_col = input_col

    # If panel doesn't include sa_input as a column, attach it via the panel index.
    if this_week_col not in valid.columns:
        valid["sa_input"] = valid.index.map(lambda d: panel.loc[d, "sa_lag0"] if "sa_lag0" in panel.columns else np.nan)
        this_week_col = "sa_input"

    valid["y_dir"] = (valid["y"] > valid[this_week_col]).astype(int)

    eval_start = pd.Timestamp(eval.eval_start)
    eval_end = valid["origin"].max() if eval.eval_end is None else pd.Timestamp(eval.eval_end)
    test_idx = valid.index[(valid["origin"] >= eval_start) & (valid["origin"] <= eval_end)]

    params = {
        "n_estimators": 800, "learning_rate": 0.04,
        "num_leaves": 15, "min_child_samples": 10,
        "subsample": 0.85, "subsample_freq": 1, "colsample_bytree": 0.85,
        "objective": "binary", "verbosity": -1, "random_state": 42,
    }
    if lgbm_params:
        params.update(lgbm_params)

    preds = []
    last_fit_idx, model = -1, None
    for i, t in enumerate(test_idx):
        train_mask = valid.index < t
        for mr in eval.train_mask_ranges:
            lo, hi = pd.Timestamp(mr[0]), pd.Timestamp(mr[1])
            train_mask = train_mask & ~((valid.index >= lo) & (valid.index <= hi))

        Xtr = valid.loc[train_mask, feature_cols].values
        ytr = valid.loc[train_mask, "y_dir"].values
        Xte = valid.loc[[t], feature_cols].values

        if model is None or i - last_fit_idx >= refit_every:
            model = lgb.LGBMClassifier(**params)
            model.fit(Xtr, ytr)
            last_fit_idx = i

        p_up = float(model.predict_proba(Xte)[0, 1])
        preds.append({
            "origin": valid.loc[t, "origin"],
            "this_week": float(valid.loc[t, this_week_col]),
            "y_true": float(valid.loc[t, "y"]),
            "y_dir": int(valid.loc[t, "y_dir"]),
            "p_up": p_up,
        })

    pred_df = pd.DataFrame(preds)
    pred_df["pred_dir"] = (pred_df["p_up"] >= 0.5).astype(int)
    pred_df["hit"] = (pred_df["pred_dir"] == pred_df["y_dir"]).astype(int)
    pred_df["abs_change"] = (pred_df["y_true"] - pred_df["this_week"]).abs()

    brier = float(((pred_df["p_up"] - pred_df["y_dir"]) ** 2).mean())
    bins = pd.cut(pred_df["p_up"], bins=[0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001], include_lowest=True)
    rel = pred_df.groupby(bins, observed=True).agg(n=("p_up", "size"), avg_p=("p_up", "mean"), emp_rate=("y_dir", "mean"))
    ece = float((rel["n"] * (rel["avg_p"] - rel["emp_rate"]).abs()).sum() / max(rel["n"].sum(), 1))

    return {
        "n": len(pred_df),
        "hit_rate": float(pred_df["hit"].mean()),
        "brier": brier,
        "ece": ece,
        "preds": pred_df,
        "reliability": rel,
        "eval_start": eval_start.date().isoformat(),
        "eval_end": eval_end.date().isoformat(),
    }


def _fit(model: str, X, y, lgbm_params, xgb_params):
    if model == "lgbm":
        import lightgbm as lgb
        params = {
            "n_estimators": 800,
            "learning_rate": 0.03,
            "num_leaves": 31,
            "min_child_samples": 20,
            "subsample": 0.85,
            "subsample_freq": 1,
            "colsample_bytree": 0.85,
            "reg_alpha": 0.0,
            "reg_lambda": 0.0,
            "objective": "regression_l1",  # MAE-aligned
            "verbosity": -1,
            "random_state": 42,
        }
        if lgbm_params:
            params.update(lgbm_params)
        m = lgb.LGBMRegressor(**params)
        m.fit(X, y)
        return m
    elif model == "xgb":
        import xgboost as xgb
        params = {
            "n_estimators": 800,
            "learning_rate": 0.03,
            "max_depth": 6,
            "min_child_weight": 5,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "reg_alpha": 0.0,
            "reg_lambda": 1.0,
            "objective": "reg:absoluteerror",
            "tree_method": "hist",
            "verbosity": 0,
            "random_state": 42,
        }
        if xgb_params:
            params.update(xgb_params)
        m = xgb.XGBRegressor(**params)
        m.fit(X, y)
        return m
    else:
        raise ValueError(f"unknown model {model!r}")


def fmt_summary(result: dict[str, Any], label: str = "") -> str:
    return (
        f"[{label:>30}] n={result['n']:3d}  "
        f"model MAE={result['model_mae']:>7,.0f}  "
        f"RMSE={result['model_rmse']:>7,.0f}  "
        f"bias={result['model_bias']:>+7,.0f}  "
        f"|  snaive MAE={result['snaive_mae']:>7,.0f}  "
        f"({result['eval_start']}..{result['eval_end']})"
    )
