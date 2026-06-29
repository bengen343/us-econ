"""Walk-forward bake-off for the Challenger headline (announced job cuts, total).

Target is modeled in logs (the level is right-skewed, 15k-670k). For every test
month we fit on all earlier non-COVID observations and produce a one-step-ahead
point forecast, then score on the back-transformed level scale.

Run:  .venv\\Scripts\\python.exe -m forecasts.challenger_employment.harness
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from forecasts.challenger_employment import data
from forecasts.challenger_employment import model as M

warnings.simplefilter("ignore")
logging.getLogger("google").setLevel(logging.ERROR)

COVID_START = data.COVID_START
COVID_END = data.COVID_END
TEST_START = pd.Timestamp("2016-01-01")  # leave ~4yr warmup for the regression models


# ---------- feature engineering ----------

def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Shared production features (model.build_features) + the seasonal lag the
    research baselines compare against."""
    df = M.build_features(panel)
    df["ly_l12"] = df["ly"].shift(12)
    return df


# Column groups for the regression specs.
SEAS_AR = ["ly_l1", "ly_l12"]
MONTH_DUMMIES = M.MONTH_DUMMIES


# ---------- model registry ----------
# Each spec: (name, kind, cols)  where kind in {baseline, ols, ridge}

_DUM_AR = ["ly_l1"] + MONTH_DUMMIES
_ALLIND = ["ism_dev", "claims_yoy", "cb_labor_differential", "mich"]

# spec = (name, kind, cols, lam)
SPECS = [
    ("rw_lastval",         "baseline", "rw", 0),
    ("seasonal_naive",     "baseline", "snaive", 0),
    ("ma3",                "baseline", "ma3", 0),
    ("seasonal_x_drift",   "baseline", "seas_drift", 0),
    ("ar1",                "ols",      ["ly_l1"], 0),
    ("ar1_seas",           "ols",      SEAS_AR, 0),
    ("seas_dummies_ar1",   "ols",      _DUM_AR, 0),
    ("seasAR_ism",         "ols",      SEAS_AR + ["ism_dev"], 0),
    ("seasdum_ar1_ism",    "ridge",    _DUM_AR + ["ism_dev"], 5.0),
    ("seasdum_ar1_ism_cb", "ridge",    _DUM_AR + ["ism_dev", "cb_labor_differential"], 5.0),
    ("seasdum_ar1_allind_l1",  "ridge", _DUM_AR + _ALLIND, 1.0),
    ("seasdum_ar1_allind_l5",  "ridge", _DUM_AR + _ALLIND, 5.0),
    ("seasdum_ar1_allind_l15", "ridge", _DUM_AR + _ALLIND, 15.0),
    ("seasdum_ar1_allind_l30", "ridge", _DUM_AR + _ALLIND, 30.0),
]

RIDGE_LAMBDA = 5.0


def _fit_predict_linear(train: pd.DataFrame, test_row: pd.Series, cols: list[str],
                        ridge: bool, lam: float = RIDGE_LAMBDA) -> float | None:
    sub = train.dropna(subset=["ly"] + cols)
    if len(sub) < max(24, 2 * len(cols) + 6):
        return None
    if test_row[cols].isna().any():
        return None
    X = sub[cols].to_numpy(float)
    y = sub["ly"].to_numpy(float)
    if ridge:
        # standardize predictors so a single lambda penalizes them comparably
        mu, sd = X.mean(0), X.std(0)
        sd[sd == 0] = 1.0
        Xs = (X - mu) / sd
        Xs = np.column_stack([np.ones(len(Xs)), Xs])
        xs = np.concatenate([[1.0], (test_row[cols].to_numpy(float) - mu) / sd])
        p = Xs.shape[1]
        R = lam * np.eye(p)
        R[0, 0] = 0.0
        beta = np.linalg.solve(Xs.T @ Xs + R, Xs.T @ y)
        return float(xs @ beta)
    X = np.column_stack([np.ones(len(X)), X])
    xt = np.concatenate([[1.0], test_row[cols].to_numpy(float)])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(xt @ beta)


def _baseline_predict(hist: pd.DataFrame, t: pd.Timestamp, kind: str) -> float | None:
    """hist = features up to and including the row at t (target unknown)."""
    y = hist["y"]
    prev = t - pd.DateOffset(months=1)
    prev12 = t - pd.DateOffset(months=12)
    if kind == "rw":
        return y.get(prev)
    if kind == "snaive":
        return y.get(prev12)
    if kind == "ma3":
        vals = [y.get(t - pd.DateOffset(months=k)) for k in (1, 2, 3)]
        vals = [v for v in vals if pd.notna(v)]
        return float(np.mean(vals)) if len(vals) == 3 else None
    if kind == "seas_drift":
        # y_{M-1} scaled by historical seasonal ratio (mean log diff month M vs M-1)
        ly = np.log(y)
        prev_v = ly.get(prev)
        if pd.isna(prev_v):
            return None
        m, pm = t.month, prev.month
        # average seasonal step from prev-month to this-month across history (< t, ex-COVID)
        past = hist[hist.index < t]
        past = past[(past.index < COVID_START) | (past.index > COVID_END)]
        steps = []
        for ts in past.index:
            a = ly.get(ts - pd.DateOffset(months=1))
            b = ly.get(ts)
            if ts.month == m and pd.notna(a) and pd.notna(b):
                steps.append(b - a)
        if not steps:
            return None
        return float(np.exp(prev_v + np.mean(steps)))
    return None


# ---------- walk-forward evaluation ----------

def predictions(feat: pd.DataFrame, kind: str, cols, lam: float = RIDGE_LAMBDA) -> pd.Series:
    """One-step-ahead level forecasts indexed by test month."""
    out = {}
    test_idx = feat.index[(feat.index >= TEST_START) & feat["y"].notna()]
    test_idx = [t for t in test_idx if not (COVID_START <= t <= COVID_END)]
    for t in test_idx:
        train = feat[feat.index < t]
        train = train[(train.index < COVID_START) | (train.index > COVID_END)]
        if kind == "baseline":
            pred = _baseline_predict(feat[feat.index <= t], t, cols)
        else:
            pred_log = _fit_predict_linear(train, feat.loc[t], cols, ridge=(kind == "ridge"), lam=lam)
            pred = float(np.exp(pred_log)) if pred_log is not None else None
        if pred is not None:
            out[t] = pred
    return pd.Series(out, name="pred")


def score(pred: pd.Series, feat: pd.DataFrame, index: pd.Index) -> dict:
    p = pred.reindex(index)
    actual = feat["y"].reindex(index)
    err = p - actual
    ape = (err.abs() / actual)
    prev = feat["y"].shift(1).reindex(index)
    d = pd.DataFrame({"p": p, "a": actual, "pv": prev}).dropna()
    dir_hit = (np.sign(d["p"] - d["pv"]) == np.sign(d["a"] - d["pv"])).mean() if len(d) else np.nan
    return {
        "n": int(p.notna().sum()),
        "MAE": err.abs().mean(),
        "RMSE": np.sqrt((err ** 2).mean()),
        "MdAE": err.abs().median(),
        "MAPE": ape.mean() * 100,
        "dir": dir_hit * 100 if pd.notna(dir_hit) else np.nan,
    }


def main() -> None:
    from google.cloud import bigquery
    client = bigquery.Client(project="us-econ-51920")
    panel = data.build_panel(client)
    feat = build_features(panel)

    preds = {name: predictions(feat, kind, cols, lam) for name, kind, cols, lam in SPECS}

    # Ensemble: mean (in log space) of the RMSE-robust and typical-month-robust specs.
    ens = pd.concat([np.log(preds["seasdum_ar1_ism"]),
                     np.log(preds["seasdum_ar1_allind_l15"])], axis=1).mean(axis=1)
    preds["ENSEMBLE"] = np.exp(ens)

    # Common test set: months every model could predict (fair RMSE comparison).
    common = None
    for p in preds.values():
        idx = p.dropna().index
        common = idx if common is None else common.intersection(idx)

    full_max = feat.index[feat["y"].notna()].max().date()
    pd.set_option("display.width", 220, "display.max_columns", 25)

    for label, idx in [("COMMON test set", common), ("NATIVE (each model's own months)", None)]:
        rows = []
        for name, p in preds.items():
            use_idx = idx if idx is not None else p.dropna().index
            s = score(p, feat, use_idx)
            s["name"] = name
            rows.append(s)
        res = pd.DataFrame(rows).set_index("name").sort_values("RMSE")
        sn = res.loc["seasonal_naive", "RMSE"]
        rw = res.loc["rw_lastval", "RMSE"]
        res["vs_snaive%"] = (res["RMSE"] / sn - 1) * 100
        res["vs_rw%"] = (res["RMSE"] / rw - 1) * 100
        print(f"\n===== {label} =====")
        if idx is not None:
            print(f"n={len(idx)} months, {idx.min().date()}..{idx.max().date()} "
                  f"(COVID {COVID_START.date()}..{COVID_END.date()} masked)")
        print(res[["n", "MAE", "RMSE", "MdAE", "MAPE", "dir", "vs_snaive%", "vs_rw%"]]
              .round({"MAE": 0, "RMSE": 0, "MdAE": 0, "MAPE": 1, "dir": 0, "vs_snaive%": 1, "vs_rw%": 1})
              .to_string())


if __name__ == "__main__":
    main()
