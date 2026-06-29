"""BigQuery pulls + forecast computation for the Challenger job-cuts job.

Reads the panel via the shared data layer and hands it to the shared model
(``forecasts.challenger_employment.model``). Returns [] (the job logs + skips)
only when the prior-month headline is not yet in BigQuery — otherwise the model
always nowcasts the next unreleased month, using the full indicator ensemble when
the month's surveys are in and the seasonal+AR(1) fallback before then.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.challenger_employment import data
from forecasts.challenger_employment.model import forecast_next
from forecasts.challenger_employment.production import config as cfg


@dataclass(frozen=True)
class Forecast:
    target: str
    target_month: pd.Timestamp
    model_version: str
    value: float
    value_rounded: float
    units: str
    n_train: int
    method: str


def compute(client: bigquery.Client) -> list[Forecast]:
    result = forecast_next(data.build_panel(client))
    if result is None:
        return []

    # Display precision: nearest 100 persons (point error is ~9-17k; finer
    # precision would overstate confidence).
    targets = [
        ("challenger_job_cuts", result.level, round(result.level / 100) * 100.0),
        ("challenger_job_cuts_change", result.change, round(result.change / 100) * 100.0),
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
            method=result.method,
        )
        for name, value, rounded in targets
    ]
