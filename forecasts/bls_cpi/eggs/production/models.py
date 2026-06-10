"""BigQuery pulls + forecast computation for the egg-price production job.

Reads the two monthly inputs latest-vintage-per-month (both append-only,
vintage-stamped tables) and hands them to the shared model
(``forecasts.bls_cpi.eggs.model``):

  * ``bls_cpi.average_prices``  -- the target, avg price of eggs ($/dozen, NSA)
  * ``bls_ppi.ppi_series``      -- PPI chicken eggs (NSA commodity series), the
                                   wholesale regressor

Returns [] (and the job logs + skips) when the next month's regressors are not
all published yet -- e.g. if the PPI collector hasn't landed the M-1 print.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.bls_cpi.eggs.model import forecast_next
from forecasts.bls_cpi.eggs.production import config as cfg


@dataclass(frozen=True)
class Forecast:
    target: str
    target_month: pd.Timestamp
    model_version: str
    value: float
    value_rounded: float
    units: str
    n_train: int


def _pull_series(client: bigquery.Client, table: str, series_id: str) -> pd.Series:
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, value,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY ingested_at DESC) AS rn
      FROM `{cfg.PROJECT}.{table}`
      WHERE series_id = @sid
    )
    SELECT observation_date, value FROM ranked WHERE rn = 1
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", series_id)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float),
        index=pd.to_datetime(frame["observation_date"]),
    )


def compute(client: bigquery.Client) -> list[Forecast]:
    panel = pd.DataFrame(
        {
            "ap": _pull_series(client, "bls_cpi.average_prices", cfg.AP_SERIES_ID),
            "ppi": _pull_series(client, "bls_ppi.ppi_series", cfg.PPI_SERIES_ID),
        }
    ).sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    return [
        Forecast(
            target="eggs_ap_level",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.level,
            value_rounded=round(result.level, 3),  # AP is published to 3 decimals
            units=cfg.TARGET_UNITS["eggs_ap_level"],
            n_train=result.n_train,
        ),
        Forecast(
            target="eggs_ap_mm",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.mm_pct,
            value_rounded=round(result.mm_pct, 1),
            units=cfg.TARGET_UNITS["eggs_ap_mm"],
            n_train=result.n_train,
        ),
    ]
