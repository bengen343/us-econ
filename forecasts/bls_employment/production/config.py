"""Production config for the Employment Situation forecasts (NFP + unemployment).

Single source of truth for the model roster and output location. The methodology
and the model choices come from the research harnesses (../payrolls_headline,
../unemployment_rate, ../midas.py, ../dfm.py): a regularised-linear winner per
target plus the configurations that were close, run as a small side-by-side
ensemble so we keep comparing them live.

Bumping any value is a deliberate model-version change — record it in the PR /
memory.
"""

from __future__ import annotations

PROJECT = "us-econ-51920"

# Append-only: each release-week run upserts one row per
# (target, target_month, as_of_date, model_version); the _current view surfaces
# the latest generation per (target, target_month, model_version). Mirrors the
# claims / ADP forecast tables.
OUTPUT_TABLE = "bls_employment.forecast_employment_situation"
OUTPUT_CURRENT_VIEW = "bls_employment.forecast_employment_situation_current"

TARGET_NFP = "nfp_headline"  # MoM change in total nonfarm payrolls, thousands
TARGET_UR = "unemployment_rate"  # civilian unemployment rate level, percent

# Shared with the research harnesses: post-GFC training, COVID masked.
TRAIN_FLOOR = "2006-01-01"
COVID_LO, COVID_HI = "2020-03-01", "2021-06-01"
RIDGE_ALPHAS = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0)
MIDAS_K = 13  # weekly lags for the U-MIDAS UR model
DFM_FACTORS, DFM_FACTOR_ORDERS = 2, 2
