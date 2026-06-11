"""BigQuery pulls + forecast computation for the ISM Manufacturing job.

Reads the winning spec's inputs via the shared data layer and hands them to
the shared model (``forecasts.ism.manufacturing_pmi.model``). Returns []
(and the job logs + skips) when the target month's surveys are incomplete --
the forecastable window opens when the month-M S&P flash lands (~21st-24th)
and the Fed-survey composite uses whichever banks have reported (the mean
skips missing banks; all four are in by the last Monday).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.ism.manufacturing_pmi.data import pull_panel_bq
from forecasts.ism.manufacturing_pmi.model import forecast_next
from forecasts.ism.manufacturing_pmi.production import config as cfg


@dataclass(frozen=True)
class Forecast:
    target: str
    target_month: pd.Timestamp
    model_version: str
    value: float
    value_rounded: float
    units: str
    n_train: int


def compute(client: bigquery.Client) -> list[Forecast]:
    result = forecast_next(pull_panel_bq(client))
    if result is None:
        return []

    # The PMI publishes to 1 decimal.
    targets = [
        ("ism_mfg_pmi", result.level, round(result.level, 1)),
        ("ism_mfg_pmi_change", result.change, round(result.change, 1)),
    ]
    return [
        Forecast(
            target=name,
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=value,
            value_rounded=rounded,
            units=cfg.TARGET_UNITS[name],
            n_train=result.n_train,
        )
        for name, value, rounded in targets
    ]
