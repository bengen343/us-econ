"""Production config for the ADP headline Pulse-bridge forecast.

Single source of truth for the model/feature choices. Bumping any value is a
deliberate model-version change — record the rationale in the PR / memory.

The methodology (established in the research harness, see ../research/README.md):
the monthly ADP headline is a derived MoM change in the SA private-employment
level, and the most predictive signal is the *weekly NER Pulse* (4-wk MA of net
weekly SA private-employment change) — it is built from the same payroll panel.
We therefore bridge the within-month Pulse readings to the monthly headline and
blend with a random-walk prior by how much of the target month has landed, so
the forecast revises every weekly (Tuesday) Pulse release.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"
MODEL_VERSION = "pulse_bridge_v1"

# ---- Output ---------------------------------------------------------------- #
# Append-only: every weekly run inserts a new revision row; the _current view
# surfaces the latest generation per target_month. Preserves the full
# weekly-revision trajectory (mirrors the claims forecast table + _current view).
OUTPUT_TABLE = "adp_employment.forecast_national_monthly"
OUTPUT_CURRENT_VIEW = "adp_employment.forecast_national_monthly_current"

# ---- Pulse publication timing --------------------------------------------- #
# The NER Pulse releases Tuesdays with a ~17-day lag (a vintage published on day
# V carries weeks ending through ~V-17). So at any refresh the target month is
# only ever partially observed; the blend handles that gracefully.
PULSE_LAG_DAYS = 17

# ---- Bridge calibration ---------------------------------------------------- #
# headline(M) ~= b * implied(M),  implied = run_rate * expected_weeks,
# run_rate = mean of available Pulse weeks in M.
#
# b is estimated as the (shrunk) mean per-month ratio headline/implied over
# calibration months with near-complete Pulse coverage. With only ~4 overlap
# months today the raw ratio (~0.83) is noisy, so we shrink toward the
# theoretically-grounded B0=1.0 (monthly change == sum of weekly SA changes if
# both products were SA-consistent) with K pseudo-observations. As real overlap
# accrues, b migrates from ~0.9 toward the empirical value.
CALIB_B0_PRIOR = 1.0
CALIB_PSEUDO_COUNT = 3.0
# A calibration month must have at least this fraction of its weeks observed to
# contribute a clean run_rate->headline ratio.
CALIB_MIN_COMPLETENESS = 0.75
# Hard floor/ceiling on the calibrated scale — guards against a degenerate fit
# from a tiny/odd sample.
CALIB_SCALE_BOUNDS = (0.5, 1.5)

# ---- Random-walk prior ----------------------------------------------------- #
# The harness found last-month's headline (RW) is the best simple monthly prior
# (MAE ~77k, ~ tied with a momentum ridge). The prior only drives the forecast
# early in the month before Pulse weeks land. PRIOR_SHRINK blends the last
# headline toward the trailing-mean of the last PRIOR_TRAIL_MONTHS; 1.0 = pure RW.
PRIOR_SHRINK = 1.0
PRIOR_TRAIL_MONTHS = 3

# ---- Blend ----------------------------------------------------------------- #
# w = completeness ** BLEND_GAMMA. GAMMA<1 would trust the Pulse run-rate earlier
# in the month; 1.0 weights linearly by fraction-of-month-observed (conservative).
BLEND_GAMMA = 1.0
