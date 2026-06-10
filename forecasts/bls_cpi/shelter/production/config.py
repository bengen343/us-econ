"""Production config for the CPI-shelter forecast.

Single source of truth for the model version and output location. The
methodology comes from the research harness (``../harness.py``) bake-off: the
plain trailing 6-month mean of the SA m/m beat every fitted spec (AR,
components, seasonal) on both test windows, and ZORI market-rent features
added nothing at the one-month horizon -- their documented 8-14 month lead is
already embodied in the trailing mean (see ``../model.py``). Deterministic, no
fitting -- mirroring the CPI dms forecast's treatment of persistent components.

Bumping ``MODEL_VERSION`` is a deliberate model change — record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only: each pre-release run upserts one row per
# (target, target_month, as_of_date, model_version); the _current view surfaces
# the latest generation per (target, target_month, model_version). Mirrors the
# CPI / eggs / gasoline forecast tables.
OUTPUT_TABLE = "bls_cpi.forecast_shelter"
OUTPUT_CURRENT_VIEW = "bls_cpi.forecast_shelter_current"

# trail6_v1 (2026-06): trailing 6-month mean of Delta-log(CUSR0000SAH1), >= 4
# months tolerated (2025 appropriations-lapse gap), chained onto the last
# published index level.
MODEL_VERSION = "trail6_v1"

# The two published representations of the same nowcast.
TARGET_UNITS: dict[str, str] = {
    "shelter_cpi_level": "index (1982-84=100, SA)",
    "shelter_cpi_mm": "percent (m/m, SA)",
}

# BigQuery input (latest vintage per month).
CPI_SERIES_ID = "CUSR0000SAH1"  # bls_cpi.cpi_series
