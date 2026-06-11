PROJECT = "us-econ-51920"

# Output (append-only with _current view for deduped latest vintage). Lives in
# the shared `ism` dataset alongside the source tables.
OUTPUT_TABLE = "ism.forecast_manufacturing_pmi"
OUTPUT_CURRENT_VIEW = "ism.forecast_manufacturing_pmi_current"

# Model version (record bumps in PR/memory):
MODEL_VERSION = "flash_fed_v1"

TARGET_UNITS = {
    "ism_mfg_pmi": "diffusion index (SA)",
    "ism_mfg_pmi_change": "points vs prior month",
}

# BigQuery inputs (latest vintage per month everywhere):
#   ism.report_on_business        -- the target PMI + components
#   fed_surveys.manufacturing_surveys -- Empire/Philly/Richmond/Dallas (+/-
#                                        balances, mapped to 50 + raw/2)
#   ism.sp_global_us_pmi          -- S&P flash manufacturing PMI (month M,
#                                    released ~21st-24th of M)
