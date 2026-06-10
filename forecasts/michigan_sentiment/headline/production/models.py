"""BigQuery pulls + forecast computation for the Michigan sentiment job.

Reads the winning specs' inputs and hands them to the shared model
(``forecasts.michigan_sentiment.headline.model``):

  * ``michigan_sentiment.surveys_of_consumers`` -- prelim + final ICS history
  * ``eia_petroleum.prices``                    -- daily Gulf Coast gasoline spot
  * ``market_indexes.daily``                    -- S&P 500 daily closes

The pending release alternates (prelim for M+1 after a final for M; final for
M after a prelim for M), so exactly one of forecast_prelim/forecast_final
returns rows per run; the other is None. Both None (start-of-history edge
cases) -> the job logs + skips.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.michigan_sentiment.headline.data import (
    pull_eia_series,
    pull_michigan,
    pull_sp500_bq,
)
from forecasts.michigan_sentiment.headline.model import (
    SentimentForecast,
    forecast_final,
    forecast_prelim,
)
from forecasts.michigan_sentiment.headline.production import config as cfg


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
    inputs = {
        "michigan": pull_michigan(client),
        "gas_spot": pull_eia_series(cfg.EIA_GAS_SPOT, client),
        "sp500": pull_sp500_bq(client),
    }

    rows: list[Forecast] = []
    for result, version in (
        (forecast_prelim(inputs), cfg.MODEL_VERSION_PRELIM),
        (forecast_final(inputs), cfg.MODEL_VERSION_FINAL),
    ):
        if result is not None:
            rows.extend(_rows(result, version))
    return rows


def _rows(result: SentimentForecast, version: str) -> list[Forecast]:
    # The ICS publishes to 1 decimal.
    targets = [
        (result.target, result.level),
        (f"{result.target}_change", result.change),
    ]
    return [
        Forecast(
            target=name,
            target_month=result.target_month,
            model_version=version,
            value=value,
            value_rounded=round(value, 1),
            units=cfg.TARGET_UNITS[name],
            n_train=result.n_train,
        )
        for name, value in targets
    ]
