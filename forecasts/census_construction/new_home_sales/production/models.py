"""BigQuery pulls + forecast computation for the new-home-sales job.

Reads the winning spec's inputs via the shared data layer and hands them to
the shared model (``forecasts.census_construction.new_home_sales.model``):

  * ``census_construction.new_residential_sales``        -- the target
  * ``census_construction.new_residential_construction`` -- same-month SF
    permits (the NRC release lands ~the 17th, a week before this print)

Returns [] (and the job logs + skips) when the target month's SF permits are
not yet published -- right after a sales release the target rolls forward
and the job idles until the next NRC release.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.census_construction.new_home_sales.data import (
    pull_sales_bq,
    pull_sf_construction_bq,
)
from forecasts.census_construction.new_home_sales.model import forecast_next
from forecasts.census_construction.new_home_sales.production import config as cfg


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
    panel = pull_sales_bq(client).join(pull_sf_construction_bq(client), how="outer").sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    # The release publishes the SAAR to whole thousands.
    targets = [
        ("new_home_sales_level", result.level, round(result.level)),
        ("new_home_sales_mm", result.mm_pct, round(result.mm_pct, 1)),
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
