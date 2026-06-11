"""BigQuery pulls + forecast computation for the core-PCE production job.

Reads the winning spec's inputs and hands them to the shared model
(``forecasts.bea_pce.core_mm.model``):

  * ``bea_pce.price_indexes``  -- the target, core PCE (DPCCRG)
  * ``bls_cpi.cpi_series``     -- core CPI SA (month M, ~10th-13th of M+1)
  * ``bls_ppi.ppi_series``     -- PPI scheduled passenger air (month M,
                                  ~11th-16th of M+1)

Returns [] (and the job logs + skips) when the target month's CPI or PPI
print has not landed yet -- right after a PCE release the target rolls
forward and the job idles until mid-month.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.bea_pce.core_mm.data import pull_bls_bq, pull_core_pce_bq
from forecasts.bea_pce.core_mm.model import forecast_next
from forecasts.bea_pce.core_mm.production import config as cfg


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
    panel = pd.DataFrame(
        {
            "core_pce": pull_core_pce_bq(client),
            "core_cpi": pull_bls_bq(cfg.CPI_CORE_ID, "bls_cpi.cpi_series", client),
            "ppi_airfares": pull_bls_bq(cfg.PPI_AIR_ID, "bls_ppi.ppi_series", client),
        }
    ).sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    # The m/m is watched to 2 decimals; the index publishes to 3.
    targets = [
        ("core_pce_mm", result.mm_pct, round(result.mm_pct, 2)),
        ("core_pce_level", result.level, round(result.level, 3)),
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
