PROJECT = "us-econ-51920"

# Output (append-only with _current view for deduped latest vintage):
OUTPUT_TABLE = "census_construction.forecast_new_home_sales"
OUTPUT_CURRENT_VIEW = "census_construction.forecast_new_home_sales_current"

# Model version (record bumps in PR/memory):
MODEL_VERSION = "perm_both_v1"

TARGET_UNITS = {
    "new_home_sales_level": "thousands of units (SAAR)",
    "new_home_sales_mm": "percent (m/m, SA)",
}

# BigQuery inputs (latest vintage per month everywhere):
#   census_construction.new_residential_sales        -- the target (sold/total/SA)
#   census_construction.new_residential_construction -- same-month SF permits
