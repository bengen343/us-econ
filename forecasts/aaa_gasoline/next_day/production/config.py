"""Production config for the AAA gasoline next-day forecast.

Single source of truth for the model versions and output locations. The
methodology comes from the research harness (``..harness``): of random walk,
AR(1), symmetric ECM, asymmetric ("rockets and feathers") ECM, and ECM+WTI, the
symmetric RBOB ECM won out of sample -- asymmetry did not help and WTI added
nothing over RBOB.

Two models run side by side (distinguished by ``model_version`` in the output):

* ``ecm_seas_mom_v1`` -- the 2026-06 review challenger: the EC term is
  re-centered on the calendar month's normal retail-RBOB wedge (the wedge swings
  ~20c/gal seasonally, which the raw EC reads as spurious disequilibrium), and
  the daily step blends the ECM drift with the latest AAA day-over-day change
  (AAA daily changes are highly persistent, AR(1) ~ +0.7; on the first month of
  live AAA data the 25/75 blend cut MAE ~20% vs the pure ECM).
* ``daily_ar_rbob_v1`` -- the 2026-10 review challenger: a daily regression fit
  on AAA's own history (two momentum lags + v2's ECM step + two RBOB daily
  changes; see ``..daily``). Out of sample on 117 live days it cut RMSE ~8% vs
  v2 (1.29c vs 1.41c).

Retired 2026-10: ``ecm_sym_rbob_v1`` (the original pure ECM, weekly move / 5). On
117 live-scored days it did worse than a no-change forecast (MAE 1.49c vs 1.36c).

2026-10 changes that kept ``ecm_seas_mom_v1``'s name (its point spec is
unchanged): RBOB now comes from settled bars dated before as_of (the morning run
had been reading Yahoo's unsettled same-day quote, ~16c low on average -- it moved
v2 <0.1c since the ECM step has a 25% weight), and its distribution is now a
Student-t scaled to its own live errors.

Bumping a model version is a deliberate model change -- record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only revision history: each daily run upserts one row per
# (target, as_of_date, model_version); the _current view surfaces the latest
# generation per (target, model_version) for the ACTIVE versions below. Mirrors
# the CPI / employment forecast tables, but keyed on a daily as_of_date rather
# than a target_month.
OUTPUT_TABLE = "aaa_gasoline.forecast_regular"
OUTPUT_CURRENT_VIEW = "aaa_gasoline.forecast_regular_current"

# Predictive distribution: half-cent probability bands of the next-day price,
# stored long-format (one row per band) alongside the point forecast.
OUTPUT_DIST_TABLE = "aaa_gasoline.forecast_regular_dist"
OUTPUT_DIST_CURRENT_VIEW = "aaa_gasoline.forecast_regular_dist_current"
DIST_BUCKET_WIDTH = 0.005  # 0.5 cent/gal bands
DIST_SPAN_SIGMAS = 6.0  # grid half-width (t(4) leaves ~0.1% of the mass outside)
# Daily errors are fat-tailed (excess kurtosis ~6), so the bands are a Student-t
# with this many degrees of freedom, scaled so its sd = the model's error RMSE.
# On 97 live days (2026-06-30..10-04) t(4) beat the Gaussian on log score
# (v2: -2.46 vs -2.61) and its 50/80/90% intervals covered ~50/80/88%.
DIST_T_DF = 4.0
# Sigma = RMSE of the model's own out-of-sample daily errors (v2: its scored live
# forecasts; daily model: its point-in-time replay errors), once this many exist.
# Below that, the weekly-ECM OOS estimate below is the fallback (it runs ~50% too
# wide for the daily models, so it is only a cold-start placeholder).
SIGMA_MIN_ERRORS = 20
# OOS residual window for the weekly-ECM fallback sigma.
SIGMA_TEST_START = "2010-01-01"

# Seasonal-EC ECM drift blended with daily AAA momentum.
MODEL_VERSION_BLEND = "ecm_seas_mom_v1"
SPEC_NAME_BLEND = "ecm_sym_seas"
ECM_WEIGHT = 0.25  # weight on the ECM's daily step (weekly move / 5)
MOMENTUM_WEIGHT = 0.75  # weight on the latest AAA day-over-day change
# Momentum needs a recent prior AAA observation; beyond this gap it is stale
# (the per-day change is averaged over the gap, and a wider gap means skip).
MOMENTUM_MAX_GAP_DAYS = 3

# Daily regression on AAA's own history (features/spec in ..daily).
MODEL_VERSION_DAILY = "daily_ar_rbob_v1"

ACTIVE_MODEL_VERSIONS = (MODEL_VERSION_BLEND, MODEL_VERSION_DAILY)

TARGET = "aaa_regular"  # AAA national-average regular retail, next-day level
UNITS = "USD/gal"
TRADING_DAYS_PER_WEEK = 5.0
