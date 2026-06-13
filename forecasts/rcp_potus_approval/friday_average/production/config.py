"""Production config for the RCP Friday approval-average forecast.

The model itself (mechanics, hyperparameters, MODEL_VERSION) lives in
``forecasts.rcp_potus_approval.friday_average.model`` — the single source of
truth shared with the research harness. This module only pins the I/O: the
project, the output table, and the latest-revision view.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only revisions: each daily run inserts one row per (target_friday,
# as_of_date); the _current view surfaces the latest as_of per target_friday,
# i.e. the freshest forecast for each upcoming Friday. Mirrors the other
# Python forecasts' forecast table + _current view convention.
OUTPUT_TABLE = "rcp_potus_approval.forecast_friday_average"
OUTPUT_CURRENT_VIEW = "rcp_potus_approval.forecast_friday_average_current"
