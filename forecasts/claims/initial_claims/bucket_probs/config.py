"""Production config for the TimesFM 3.0 bucket-probability forecast.

Every value here is a deliberate choice; changing one is a model-version
change. Record the rationale in the package README.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"
MODEL_VERSION = "tfm3_qm_v1"          # TimesFM 3.0 + quantile-matched two-piece normal

# --- Forecast ------------------------------------------------------------- #
# AI.FORECAST model string, exactly as BigQuery expects it.
# Deliberately 3.0 while the level forecast runs 2.5: 2.5 has the better MAE
# and Brier, but its 95% interval only covers 91.6% and 7 of 119 actuals landed
# above its 97.5th percentile (expect ~3). The misses in this series are
# positive spikes, so a thin upper tail underprices exactly the far "At least N"
# strikes. See the README before changing this.
TIMESFM_MODEL = "TimesFM 3.0"
# Same 2023-01-01 floor the production ARIMA uses. TimesFM is zero-shot so the
# floor only sets context length; 2023 keeps it on the post-COVID regime and
# matches the existing level forecast's basis.
TRAIN_FLOOR = "2023-01-01"
HORIZONS = 4                          # h=1 is the tradeable one; 2-4 are context
CONFIDENCE_LEVEL = 0.95               # AI.FORECAST default; bounds are the 2.5/97.5 quantiles

# --- Kalshi contract geometry --------------------------------------------- #
# KXJOBLESSCLAIMS lists ~11 "At least N" strikes per weekly event, spaced 5,000
# apart on round multiples of 5,000, re-centred each week. We emit a wider
# symmetric grid so the listed strikes are always covered.
BUCKET_WIDTH = 5000.0
N_STRIKES_EACH_SIDE = 6               # 13 strikes = +/-30k around the point forecast
# DOL publishes the headline rounded to the nearest 1,000, and Kalshi settles on
# that published figure -- so P(>=195,000) is P(underlying >= 194,500).
ROUNDING_UNIT = 1000.0

# --- Freshness guard ------------------------------------------------------ #
# The claims collector lands Thursday ~12:45 UTC and this job runs 17:15 UTC the
# same day, so the origin should never be more than a week old. Fail loudly
# rather than quietly forecast from a stale origin: a silently stale forecast is
# worse than no forecast when it is priced against a live market.
MAX_ORIGIN_AGE_DAYS = 10

# --- Output --------------------------------------------------------------- #
OUTPUT_TABLE = "claims.forecast_claims_buckets"
OUTPUT_CURRENT_VIEW = "claims.forecast_claims_buckets_current"
