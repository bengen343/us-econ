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

# dms_v1: deterministic bottom-up reconstruction (Cleveland-Fed DMS).
# dms_v2 (2026-06): the CORE targets get a Manheim-driven used-cars adjustment —
#   used cars are nowcast from the wholesale Manheim index (walk-forward OLS on
#   1-2 month lags) and core's trailing average is shifted by HALF the weighted
#   used-cars surprise (half because the surprise only partially transfers to
#   the core aggregate; backtest lambda* ~ +0.45). The headline targets keep the
#   plain trailing core (lambda* ~ 0 at headline — used-cars surprises wash out
#   against food/energy interactions). Falls back to the dms_v1 form when
#   Manheim is unavailable.
MODEL_VERSION = "dms_v2"

# The four published targets and their units.
TARGET_UNITS: dict[str, str] = {
    "headline_mm": "percent (m/m, SA)",
    "core_mm": "percent (m/m, SA)",
    "headline_yy": "percent (y/y)",
    "core_yy": "percent (y/y)",
}
