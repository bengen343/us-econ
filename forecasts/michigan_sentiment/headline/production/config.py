PROJECT = "us-econ-51920"

# Output (append-only with _current view for deduped latest vintage):
OUTPUT_TABLE = "michigan_sentiment.forecast_headline"
OUTPUT_CURRENT_VIEW = "michigan_sentiment.forecast_headline_current"

# Model versions (record bumps in PR/memory). One job, two targets: the
# pending release alternates prelim -> final -> prelim, so each run emits
# exactly one target's rows.
MODEL_VERSION_PRELIM = "gas_sp_sw_v1"
MODEL_VERSION_FINAL = "gas_sp_post_v1"

TARGET_UNITS = {
    "ics_prelim": "index (1966:Q1=100)",
    "ics_prelim_change": "points vs prior final",
    "ics_final": "index (1966:Q1=100)",
    "ics_final_change": "points vs the preliminary (revision)",
}

# BigQuery inputs:
EIA_GAS_SPOT = "EER_EPMRU_PF4_RGC_DPG"  # eia_petroleum.prices, daily Gulf Coast
SP500_TICKER = "^GSPC"  # market_indexes.daily
# michigan_sentiment.surveys_of_consumers -- the target series (prelim+final)
