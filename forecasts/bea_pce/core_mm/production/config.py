PROJECT = "us-econ-51920"

# Output (append-only with _current view for deduped latest vintage):
OUTPUT_TABLE = "bea_pce.forecast_core_pce"
OUTPUT_CURRENT_VIEW = "bea_pce.forecast_core_pce_current"

# Model version (record bumps in PR/memory):
MODEL_VERSION = "ccpi_air_v1"

TARGET_UNITS = {
    "core_pce_mm": "percent (m/m, SA)",
    "core_pce_level": "index (2017=100, SA)",
}

# BigQuery inputs (latest vintage per month everywhere):
PCE_CORE_CODE = "DPCCRG"  # bea_pce.price_indexes -- the target
CPI_CORE_ID = "CUSR0000SA0L1E"  # bls_cpi.cpi_series -- the translation backbone
PPI_AIR_ID = "PCU481111481111"  # bls_ppi.ppi_series -- the CPI/PCE wedge add-on
