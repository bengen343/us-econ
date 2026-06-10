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
# v1: single shrunk scale (fixed pseudo-count toward B0=1.0).
# v2 (2026-06): maturity-dependent calibration — the shrinkage toward B0 decays
#   with the target month's Pulse completeness (theory-prior early, empirical
#   post-break pooled ratio late), plus a benchmark-break calibration floor.
#   Motivation: Pulse vintages never revise (no revision curve exists to learn),
#   and the live headline/implied ratio sat ~0.72-0.75 while the B0=1.0 prior
#   dragged the scale to ~0.9 — the late-month forecasts systematically
#   overshot (Apr/May 2026). LOO across maturities: all-maturity MAE 16.5k vs
#   18.1k (fixed k) vs 16.9k (no prior at all).
MODEL_VERSION = "pulse_bridge_v2"

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
# b is estimated as the pooled ratio headline/implied over calibration months
# with near-complete Pulse coverage, shrunk toward the theoretically-grounded
# B0=1.0 (monthly change == sum of weekly SA changes if both products were
# SA-consistent). The shrinkage is MATURITY-DEPENDENT: the effective
# pseudo-count is CALIB_PSEUDO_COUNT * (1 - completeness of the target month's
# Pulse coverage). Early in the month — when the run-rate rests on 1-2 noisy
# weeks and the RW prior dominates the blend anyway — b stays anchored near
# B0; by month-end b converges to the raw post-break pooled ratio (~0.73 on
# 2026 overlap months), which the live Apr/May errors showed is what the
# late-month forecast should trust.
CALIB_B0_PRIOR = 1.0
CALIB_PSEUDO_COUNT = 3.0

# Calibration months before this floor are excluded: ADP benchmark/seasonal
# restatements (annual QCEW benchmark, flowed into the weekly series with the
# January 2026 NER) shift the headline/Pulse relationship discretely, so
# pre-break ratio pairs would pollute the post-break scale. Bump this at each
# future benchmark restatement.
CALIB_FLOOR_MONTH = "2026-01-01"
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
