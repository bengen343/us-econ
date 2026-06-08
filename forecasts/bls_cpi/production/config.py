"""Production config for the CPI forecast.

Single source of truth for the model version and output location. The methodology
comes from the research harness (``../dms``) and its bake-off (``../bench.py``):
the deterministic Cleveland-Fed-style bottom-up reconstruction won outright over
fitted regression, U-MIDAS, and a dynamic factor model, so production ships it
alone (no ensemble).

Bumping ``MODEL_VERSION`` is a deliberate model change — record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only: each pre-release run upserts one row per
# (target, target_month, as_of_date, model_version); the _current view surfaces
# the latest generation per (target, target_month, model_version). Mirrors the
# employment-situation / claims forecast tables.
OUTPUT_TABLE = "bls_cpi.forecast_cpi"
OUTPUT_CURRENT_VIEW = "bls_cpi.forecast_cpi_current"

MODEL_VERSION = "dms_v1"  # deterministic bottom-up reconstruction (Cleveland-Fed DMS)

# The four published targets and their units.
TARGET_UNITS: dict[str, str] = {
    "headline_mm": "percent (m/m, SA)",
    "core_mm": "percent (m/m, SA)",
    "headline_yy": "percent (y/y)",
    "core_yy": "percent (y/y)",
}
