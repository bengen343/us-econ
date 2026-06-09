"""Production config for the AAA gasoline next-day forecast.

Single source of truth for the model versions and output locations. The
methodology comes from the research harness (``..harness``): of random walk,
AR(1), symmetric ECM, asymmetric ("rockets and feathers") ECM, and ECM+WTI, the
symmetric RBOB ECM won out of sample -- asymmetry did not help and WTI added
nothing over RBOB.

Two models run side by side (distinguished by ``model_version`` in the output):

* ``ecm_sym_rbob_v1`` -- the original symmetric RBOB ECM, weekly move / 5.
* ``ecm_seas_mom_v1`` -- the 2026-06 review challenger: the EC term is
  re-centered on the calendar month's normal retail-RBOB wedge (the wedge swings
  ~20c/gal seasonally, which the raw EC reads as spurious disequilibrium), and
  the daily step blends the ECM drift with the latest AAA day-over-day change
  (AAA daily changes are highly persistent, AR(1) ~ +0.7; on the first month of
  live AAA data the 25/75 blend cut MAE ~20% vs the pure ECM).

Bumping a model version is a deliberate model change -- record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only revision history: each daily run upserts one row per
# (target, as_of_date, model_version); the _current view surfaces the latest
# generation per (target, model_version). Mirrors the CPI / employment forecast
# tables, but keyed on a daily as_of_date rather than a target_month.
OUTPUT_TABLE = "aaa_gasoline.forecast_regular"
OUTPUT_CURRENT_VIEW = "aaa_gasoline.forecast_regular_current"

# Predictive distribution: half-cent probability bands of the next-day price,
# stored long-format (one row per band) alongside the point forecast.
OUTPUT_DIST_TABLE = "aaa_gasoline.forecast_regular_dist"
OUTPUT_DIST_CURRENT_VIEW = "aaa_gasoline.forecast_regular_dist_current"
DIST_BUCKET_WIDTH = 0.005  # 0.5 cent/gal bands
DIST_SPAN_SIGMAS = 4.0  # grid half-width (captures ~99.99% of the mass)
# OOS residual window used to estimate the daily forecast sigma (recent + cheap).
SIGMA_TEST_START = "2010-01-01"

MODEL_VERSION = "ecm_sym_rbob_v1"  # symmetric RBOB error-correction model
SPEC_NAME = "ecm_sym"  # the model.SPECS entry to ship

# Challenger: seasonal-EC ECM drift blended with daily AAA momentum.
MODEL_VERSION_BLEND = "ecm_seas_mom_v1"
SPEC_NAME_BLEND = "ecm_sym_seas"
ECM_WEIGHT = 0.25  # weight on the ECM's daily step (weekly move / 5)
MOMENTUM_WEIGHT = 0.75  # weight on the latest AAA day-over-day change
# Momentum needs a recent prior AAA observation; beyond this gap it is stale
# (the per-day change is averaged over the gap, and a wider gap means skip).
MOMENTUM_MAX_GAP_DAYS = 3

TARGET = "aaa_regular"  # AAA national-average regular retail, next-day level
UNITS = "USD/gal"
TRADING_DAYS_PER_WEEK = 5.0
