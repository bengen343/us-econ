PROJECT = "us-econ-51920"

# Output (append-only with _current view for deduped latest vintage):
OUTPUT_TABLE = "census_construction.forecast_starts_permits"
OUTPUT_CURRENT_VIEW = "census_construction.forecast_starts_permits_current"

# Model versions (record bumps in PR/memory). One job, two targets that
# publish together in the New Residential Construction release.
MODEL_VERSION_STARTS = "ecm_hmi_wx_v1"
MODEL_VERSION_PERMITS = "sf_mf_split_v1"

TARGET_UNITS = {
    "starts_level": "thousands of units (SAAR)",
    "starts_mm": "percent (m/m, SA)",
    "permits_level": "thousands of units (SAAR)",
    "permits_mm": "percent (m/m, SA)",
}

# BigQuery inputs (latest vintage per month everywhere):
#   census_construction.new_residential_construction -- starts + permits
#   nahb_hmi.housing_market_index                     -- month-M HMI
#   noaa_climate.climate_at_a_glance                  -- month-M temperature
