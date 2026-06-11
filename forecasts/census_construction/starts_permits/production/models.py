"""BigQuery pulls + forecast computation for the starts/permits job.

Reads the winning specs' inputs and hands them to the shared model
(``forecasts.census_construction.starts_permits.model``):

  * ``census_construction.new_residential_construction`` -- starts + permits
    (SA, total + segments; latest vintage per month)
  * ``nahb_hmi.housing_market_index``  -- month-M HMI (released ~16th of M)
  * ``noaa_climate.climate_at_a_glance`` -- month-M temperature (~8th of M+1)

Both targets publish together in the NRC release (~16th-19th of M+1), but
their regressors complete at different times: the permits spec needs only
M-1 Census data (available right after the prior release), while the starts
spec waits for the month-M temperature (~the 9th of M+1). forecast_* return
None for whichever target's regressors are incomplete and the job loads what
it has.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.census_construction.starts_permits.data import (
    pull_census_bq,
    pull_hmi_bq,
    pull_tavg_bq,
)
from forecasts.census_construction.starts_permits.model import (
    ConstructionForecast,
    forecast_permits,
    forecast_starts,
)
from forecasts.census_construction.starts_permits.production import config as cfg


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
        "starts": pull_census_bq("starts", client),
        "permits": pull_census_bq("permits", client),
        "hmi": pull_hmi_bq(client),
        "tavg": pull_tavg_bq(client),
    }

    rows: list[Forecast] = []
    for result, version in (
        (forecast_starts(inputs), cfg.MODEL_VERSION_STARTS),
        (forecast_permits(inputs), cfg.MODEL_VERSION_PERMITS),
    ):
        if result is not None:
            rows.extend(_rows(result, version))
    return rows


def _rows(result: ConstructionForecast, version: str) -> list[Forecast]:
    # The release publishes levels to whole thousands and m/m to 1 decimal.
    targets = [
        (f"{result.target}_level", result.level, round(result.level)),
        (f"{result.target}_mm", result.mm_pct, round(result.mm_pct, 1)),
    ]
    return [
        Forecast(
            target=name,
            target_month=result.target_month,
            model_version=version,
            value=value,
            value_rounded=rounded,
            units=cfg.TARGET_UNITS[name],
            n_train=result.n_train,
        )
        for name, value, rounded in targets
    ]
