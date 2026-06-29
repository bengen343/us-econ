PROJECT = "us-econ-51920"

# Output (append-only with a _current view for the latest vintage). Lives in the
# shared `challenger_employment` dataset alongside the source tables.
OUTPUT_TABLE = "challenger_employment.forecast_job_cuts"
OUTPUT_CURRENT_VIEW = "challenger_employment.forecast_job_cuts_current"

# Model version (record bumps in PR/memory):
MODEL_VERSION = "seas_ensemble_v1"

TARGET_UNITS = {
    "challenger_job_cuts": "announced job cuts (persons, NSA)",
    "challenger_job_cuts_change": "persons vs prior month",
}

# BigQuery inputs (read-only):
#   challenger_employment.monthly            -- target headline (layoffs/total),
#                                               live collector + Wayback backfill
#   claims.weekly_claims                     -- national NSA initial claims
#   ism.report_on_business                   -- ISM mfg employment index
#   conference_board.consumer_confidence     -- labor differential
#   michigan_sentiment.surveys_of_consumers  -- consumer sentiment (final)
