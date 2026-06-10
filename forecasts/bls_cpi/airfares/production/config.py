"""Production config for the airline-fares forecast.

Single source of truth for the model version and output location. The
methodology comes from the research harness (``../harness.py``) bake-off:
OLS of Delta-log(SA airfares index) on its own first two lags (airfares m/m
mean-reverts) plus WTI changes at lags 0-2 (complete-month means). It cut the
carry-forward baseline's m/m RMSE by ~22% on 2010-2026 COVID-masked origins;
PPI airline (industry + commodity) and jet-fuel specs all scored worse (see
``../model.py``).

Bumping ``MODEL_VERSION`` is a deliberate model change — record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only: each pre-release run upserts one row per
# (target, target_month, as_of_date, model_version); the _current view surfaces
# the latest generation per (target, target_month, model_version). Mirrors the
# other sub-series forecast tables.
OUTPUT_TABLE = "bls_cpi.forecast_airfares"
OUTPUT_CURRENT_VIEW = "bls_cpi.forecast_airfares_current"

# ar2_wti_v1 (2026-06): OLS of Delta-log(CUSR0000SETG01, month M) on
#   [1, Delta-log idx_{M-1}, Delta-log idx_{M-2},
#    Delta-log(WTI complete-month means, M / M-1 / M-2)].
# Month M's WTI is fully published before the mid-(M+1) CPI release (daily
# spot), so the contemporaneous term is PIT-clean -- same timing argument as
# the gasoline forecast.
MODEL_VERSION = "ar2_wti_v1"

# The two published representations of the same nowcast.
TARGET_UNITS: dict[str, str] = {
    "airfares_cpi_level": "index (1982-84=100, SA)",
    "airfares_cpi_mm": "percent (m/m, SA)",
}

# BigQuery inputs (latest vintage per month).
CPI_SERIES_ID = "CUSR0000SETG01"  # bls_cpi.cpi_series
EIA_SERIES_ID = "RWTC"  # eia_petroleum.prices (WTI daily spot, $/bbl)
