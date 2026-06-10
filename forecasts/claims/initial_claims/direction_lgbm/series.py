"""Production config for the direction-forecast Cloud Run Job.

This file is the single point of truth for the model/feature choices made
during the lgbm_test exploration. Bumping any of these values is a
deliberate model-version change; record the rationale in CLAUDE.md or the
PR description.

The HPO winners below were established by the iter-13 sweep across feature
sets and hyperparameters (5 feature sets * 108 LGBM configs each). The
winning combination (ADP NSA WoW-diff at lag 8 + Google Trends at lag 4,
HPO-tuned LGBM binary classifier) achieved 69.07% h=1 direction-hit-rate
over n=97 walk-forward origins from 2024-07-06..2026-05-09.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"
FEATURE_SET_NAME = "adp+tr4"

# Training window: post-COVID regime only. Pre-2022 data hurt the model in
# iter-7 even with explicit COVID masking.
TRAIN_FLOOR = "2022-01-01"

# HPO-winning LGBM hyperparameters from iter-13 on the adp+tr4 feature set.
LGBM_PARAMS = {
    "n_estimators": 3000,
    "learning_rate": 0.06,
    "num_leaves": 8,
    "min_child_samples": 20,
    "subsample": 0.85,
    "subsample_freq": 1,
    "colsample_bytree": 0.85,
    "objective": "binary",
    "verbosity": -1,
    "random_state": 42,
}

# Lag-8 of ADP NSA NER (WoW diff) was the iter-8 winner; the publication lag
# is ~53 days so lag-8 is the freshest valid alignment in normal conditions.
# Fall back to lag-9, lag-10, etc. if the freshest ADP row is older than
# expected. (Lag is in weeks.)
ADP_DIFF_LAG_PRIMARY = 8
ADP_DIFF_LAG_FALLBACKS = [9, 10, 11, 12]
ADP_NER_COL = "ner"  # NSA total US employment level

# Trends signal that won iter-2: 5-week-old searches predict next-week claims.
# All 11 collected Trends terms participate at this single lag.
TRENDS_LAG = 4

# Target lags 1..8 + seasonal (52-week + 51-week reference) match the iter-11
# floor that all post-floor iterations built on.
TARGET_LAGS = list(range(1, 9))

# Calibration: walk-forward Platt scaling (logistic on the raw logit) over ALL
# prior held-out predictions (expanding window), output clipped. Replaced the
# original 26-week rolling isotonic in 2026-06: isotonic needs ~1000+ points
# and on 26 it mapped extreme bins to empirical 0/1 — production emitted
# p_up=0.001 the week claims rose 13k. Out-of-sample over 150 origins: Platt
# Brier 0.249 / logloss 0.69 / zero extreme-and-wrong calls, vs isotonic-26
# 0.289 / 1.76 / 8, vs raw 0.358 / 2.16 / 36. The expanding refit loop costs
# ~0.3s per origin (~1 min today), well inside the 600s job budget.
# pred_dir_up stays thresholded on the RAW probability (the iter-13
# point-accuracy choice); Platt is monotone so only the implicit threshold
# would differ, and the calibrated channel exists for honest confidence.
CALIBRATION_MIN_ORIGINS = 30  # below this, fall back to the (clipped) raw p
CALIBRATION_CLIP = (0.05, 0.95)

# BQ table holding both the existing level forecast and the new direction
# columns (added by 11_direction_columns_migration.sql).
OUTPUT_TABLE = "claims.forecast_sa_initial_claims"
