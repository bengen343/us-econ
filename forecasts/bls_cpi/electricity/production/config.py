"""Production config for the electricity-price forecast.

Single source of truth for the model version and output location. The
methodology comes from the research harness (``../harness.py``) bake-off:
pure own-history OLS -- AR(1) + year-ago m/m + expanding calendar-month
seasonal mean -- on Delta-log prices. It cut the random walk's m/m RMSE by
~43% on 2010-2026 COVID-masked origins; every PPI-electric-power and Henry
Hub natural-gas spec scored worse (administered retail rates move through
rate cases, so producer/fuel prices carry no exploitable signal at h=1 --
see ``../model.py``).

Bumping ``MODEL_VERSION`` is a deliberate model change — record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only: each pre-release run upserts one row per
# (target, target_month, as_of_date, model_version); the _current view surfaces
# the latest generation per (target, target_month, model_version). Mirrors the
# CPI / eggs / gasoline / shelter forecast tables.
OUTPUT_TABLE = "bls_cpi.forecast_electricity"
OUTPUT_CURRENT_VIEW = "bls_cpi.forecast_electricity_current"

# seasonal_ar12_v1 (2026-06): OLS of Delta-log(avg electricity price, month M)
# on [1, Delta-log AP_{M-1}, Delta-log AP_{M-12}, expanding calendar-month
# mean]. Own-history only -- the strong NSA seasonality (summer rate
# schedules) plus persistence carries all the h=1 signal.
MODEL_VERSION = "seasonal_ar12_v1"

# The two published representations of the same nowcast.
TARGET_UNITS: dict[str, str] = {
    "electricity_ap_level": "USD per kWh (NSA)",
    "electricity_ap_mm": "percent (m/m, NSA)",
}

# BigQuery input (latest vintage per month).
AP_SERIES_ID = "APU000072610"  # bls_cpi.average_prices
