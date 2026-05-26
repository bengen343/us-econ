"""LGBM training + prediction + walk-forward calibration."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from forecasts.claims.initial_claims.direction_lgbm.series import (
    CALIBRATION_WINDOW_WEEKS,
    LGBM_PARAMS,
    TRAIN_FLOOR,
)


def _fit(panel: pd.DataFrame, feature_cols: list[str]) -> lgb.LGBMClassifier:
    """Fit one LGBM binary classifier on every row of `panel` (assumed already
    trimmed and inside the training window)."""
    X = panel[feature_cols].values
    y = panel["y_dir"].astype(int).values
    model = lgb.LGBMClassifier(**LGBM_PARAMS)
    model.fit(X, y)
    return model


def _walk_forward_calibration_history(
    panel: pd.DataFrame, feature_cols: list[str], target_origin: pd.Timestamp,
) -> pd.DataFrame:
    """Re-derive predictions over the last CALIBRATION_WINDOW_WEEKS origins
    prior to `target_origin`, refitting walk-forward, so we have a recent
    (raw_p_up, y_dir) history to fit isotonic calibration on.

    This is the same loop as iter-13's walk-forward eval but bounded to a
    recent window — enough history to calibrate, not so much that we burn
    minutes refitting LGBM.
    """
    panel = panel.sort_index().copy()
    cutoff = target_origin - pd.Timedelta(days=7)  # last calibration origin
    cal_start = cutoff - pd.Timedelta(days=7 * (CALIBRATION_WINDOW_WEEKS - 1))

    # Eligible calibration origins: rows in [cal_start, cutoff] with non-null y_dir.
    cal_origins = panel.index[
        (panel.index >= cal_start)
        & (panel.index <= cutoff)
        & (panel["y_dir"].notna())
    ]

    rows = []
    train_floor = pd.Timestamp(TRAIN_FLOOR)
    for t in cal_origins:
        train_panel = panel.loc[
            (panel.index >= train_floor)
            & (panel.index < t)
            & (panel["y_dir"].notna())
        ]
        if len(train_panel) < 50:
            continue  # need a minimum training body
        model = _fit(train_panel, feature_cols)
        Xte = panel.loc[[t], feature_cols].values
        raw_p = float(model.predict_proba(Xte)[0, 1])
        rows.append({
            "origin": t,
            "raw_p_up": raw_p,
            "y_dir": int(panel.loc[t, "y_dir"]),
        })
    return pd.DataFrame(rows)


def fit_predict_and_calibrate(
    panel: pd.DataFrame, feature_cols: list[str], target_origin: pd.Timestamp,
) -> dict:
    """Run the full production model flow for one origin.

    Steps:
      1. Build a walk-forward calibration history over the last N weeks.
      2. Fit the final model on ALL eligible training data through target_origin.
      3. Predict raw P(up) for the row at target_origin.
      4. Fit isotonic on the calibration history and apply to raw P(up).

    Returns dict with raw probability, calibrated probability, predicted
    direction, training-row count, and the resolved feature row's audit info.
    """
    train_floor = pd.Timestamp(TRAIN_FLOOR)
    train_panel = panel.loc[
        (panel.index >= train_floor)
        & (panel.index < target_origin)
        & (panel["y_dir"].notna())
    ]
    if len(train_panel) < 50:
        raise RuntimeError(
            f"Refusing to train: only {len(train_panel)} training rows in "
            f"[{train_floor.date()}, {target_origin.date()}); minimum is 50."
        )

    # Calibration history (uses its own walk-forward refits)
    cal_hist = _walk_forward_calibration_history(panel, feature_cols, target_origin)

    # Final-fit model on all training data through (but not including) target_origin
    final_model = _fit(train_panel, feature_cols)

    if target_origin not in panel.index:
        raise RuntimeError(f"target_origin {target_origin.date()} not in panel index")
    Xte = panel.loc[[target_origin], feature_cols].values
    raw_p = float(final_model.predict_proba(Xte)[0, 1])

    # Isotonic calibration
    calibrated_p = raw_p  # fallback if history too short
    if len(cal_hist) >= 8:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(cal_hist["raw_p_up"].values, cal_hist["y_dir"].values)
        calibrated_p = float(iso.predict([raw_p])[0])

    return {
        "p_up_raw": raw_p,
        "p_up_calibrated": calibrated_p,
        "pred_dir_up": raw_p >= 0.5,
        "n_train_origins": int(len(train_panel)),
        "n_calibration_origins": int(len(cal_hist)),
    }
