"""Production config for the egg-price forecast.

Single source of truth for the model version and output location. The
methodology comes from the research harness (``../harness.py``) bake-off:
an AR(1) + wholesale (PPI chicken eggs) distributed lag + expanding NSA
seasonal mean, fit by OLS on Delta-log prices. It beat the random walk by ~18%
m/m RMSE on 2010-2026 COVID-masked origins; ECM/asymmetric pass-through terms,
SARIMA, and LightGBM all scored worse (see ``../model.py`` for the spec).

Bumping ``MODEL_VERSION`` is a deliberate model change — record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only: each pre-release run upserts one row per
# (target, target_month, as_of_date, model_version); the _current view surfaces
# the latest generation per (target, target_month, model_version). Mirrors the
# CPI forecast table.
OUTPUT_TABLE = "bls_cpi.forecast_eggs"
OUTPUT_CURRENT_VIEW = "bls_cpi.forecast_eggs_current"

# ppi_dl3_seas_v1 (2026-06): OLS of Delta-log(avg egg price, month M) on
#   [1, Delta-log AP_{M-1}, Delta-log PPI eggs_{M-1..M-3}, expanding
#    calendar-month mean] -- wholesale lags because retail follows wholesale by
#   2-5 weeks and the M-1 PPI is the latest print published at the origin.
MODEL_VERSION = "ppi_dl3_seas_v1"

# The two published representations of the same nowcast.
TARGET_UNITS: dict[str, str] = {
    "eggs_ap_level": "USD per dozen (NSA)",
    "eggs_ap_mm": "percent (m/m, NSA)",
}

# BigQuery inputs (latest vintage per month).
AP_SERIES_ID = "APU0000708111"  # bls_cpi.average_prices
PPI_SERIES_ID = "WPU017107"  # bls_ppi.ppi_series (commodity: chicken eggs)
