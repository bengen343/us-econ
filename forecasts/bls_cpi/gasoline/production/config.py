"""Production config for the CPI-gasoline forecast.

Single source of truth for the model version and output location. The
methodology comes from the research harness (``../harness.py``) bake-off:
OLS of Delta-log(SA gasoline index) on the contemporaneous month's retail
price change (EIA weekly, complete-month mean) plus the expanding
calendar-month SA wedge. It cut the random walk's m/m RMSE by ~65% on
2010-2026 COVID-masked origins; distributed-lag/AR additions and the
deterministic dms pass-through all scored (slightly) worse (see ``../model.py``).

Bumping ``MODEL_VERSION`` is a deliberate model change — record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only: each pre-release run upserts one row per
# (target, target_month, as_of_date, model_version); the _current view surfaces
# the latest generation per (target, target_month, model_version). Mirrors the
# CPI and eggs forecast tables.
OUTPUT_TABLE = "bls_cpi.forecast_gasoline"
OUTPUT_CURRENT_VIEW = "bls_cpi.forecast_gasoline_current"

# eia_wedge_v1 (2026-06): OLS of Delta-log(CUSR0000SETB01, month M) on
#   [1, Delta-log(EIA all-grades retail, complete-month-M mean), expanding
#    calendar-month mean of (target - retail change)]. The retail regressor is
#   contemporaneous: BLS computes the gasoline index from the calendar month's
#   own pump prices, all published before the CPI release.
MODEL_VERSION = "eia_wedge_v1"

# The two published representations of the same nowcast.
TARGET_UNITS: dict[str, str] = {
    "gas_cpi_level": "index (1982-84=100, SA)",
    "gas_cpi_mm": "percent (m/m, SA)",
}

# BigQuery inputs (latest vintage per month).
CPI_SERIES_ID = "CUSR0000SETB01"  # bls_cpi.cpi_series
EIA_SERIES_ID = "EMM_EPM0_PTE_NUS_DPG"  # eia_petroleum.prices (weekly, $/gal)
