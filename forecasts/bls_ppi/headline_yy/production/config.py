PROJECT = "us-econ-51920"

# Output (append-only with _current view for deduped latest vintage):
OUTPUT_TABLE = "bls_ppi.forecast_headline"
OUTPUT_CURRENT_VIEW = "bls_ppi.forecast_headline_current"

# Model version (record bumps in PR/memory):
MODEL_VERSION = "gas_dsl_ism_v1"

# Target units. The headline y/y is computed from the NSA index per BLS
# convention; the index level and m/m are published alongside it.
TARGET_UNITS = {
    "ppi_fd_yy": "percent (y/y, NSA)",
    "ppi_fd_mm": "percent (m/m, NSA)",
    "ppi_fd_level": "index (Nov 2009=100, NSA)",
}

# BigQuery inputs (latest vintage per month for the BLS series):
PPI_NSA_ID = "WPUFD4"  # bls_ppi.ppi_series -- the target index
PPI_SA_ID = "WPSFD4"  # bls_ppi.ppi_series -- SA lag-1 regressor
EIA_GAS_SPOT = "EER_EPMRU_PF4_RGC_DPG"  # eia_petroleum.prices, daily Gulf Coast
EIA_DIESEL_WEEKLY = "EMD_EPD2D_PTE_NUS_DPG"  # eia_petroleum.prices, weekly retail
ISM_REPORT = "manufacturing"  # ism.report_on_business, measure='prices'
