"""Production config for the AAA gasoline next-day forecast.

Single source of truth for the model version and output location. The methodology
comes from the research harness (``..harness``): of random walk, AR(1), symmetric
ECM, asymmetric ("rockets and feathers") ECM, and ECM+WTI, the symmetric RBOB ECM
won out of sample -- asymmetry did not help and WTI added nothing over RBOB -- so
production ships it alone (no ensemble).

Bumping ``MODEL_VERSION`` is a deliberate model change -- record it in the PR / memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only revision history: each daily run upserts one row per
# (target, as_of_date, model_version); the _current view surfaces the latest
# generation per (target, model_version). Mirrors the CPI / employment forecast
# tables, but keyed on a daily as_of_date rather than a target_month.
OUTPUT_TABLE = "aaa_gasoline.forecast_regular"
OUTPUT_CURRENT_VIEW = "aaa_gasoline.forecast_regular_current"

MODEL_VERSION = "ecm_sym_rbob_v1"  # symmetric RBOB error-correction model
SPEC_NAME = "ecm_sym"  # the model.SPECS entry to ship

TARGET = "aaa_regular"  # AAA national-average regular retail, next-day level
UNITS = "USD/gal"
TRADING_DAYS_PER_WEEK = 5.0
