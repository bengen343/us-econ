"""BigQuery pulls + forecast computation for the airline-fares production job.

Reads the two inputs and hands them to the shared model
(``forecasts.bls_cpi.airfares.model``):

  * ``bls_cpi.cpi_series``     -- the target, CPI airline fares SA index
                                  (latest vintage per month; append-only table)
  * ``eia_petroleum.prices``   -- WTI daily spot (clean upserted table),
                                  aggregated to complete-month means here

Returns [] (and the job logs + skips) when the target month's WTI is not
complete yet -- in particular, in the few days right after a CPI release the
next target month is still in progress, and the job correctly idles until the
1st of the following month.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.bls_cpi.airfares.data import monthly_mean_complete
from forecasts.bls_cpi.airfares.model import forecast_next
from forecasts.bls_cpi.airfares.production import config as cfg


@dataclass(frozen=True)
class Forecast:
    target: str
    target_month: pd.Timestamp
    model_version: str
    value: float
    value_rounded: float
    units: str
    n_train: int


def _pull_series(client: bigquery.Client, sql: str, series_id: str) -> pd.Series:
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", series_id)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float),
        index=pd.to_datetime(frame["observation_date"]),
    )


def _pull_cpi_sa(client: bigquery.Client) -> pd.Series:
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, value,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY ingested_at DESC) AS rn
      FROM `{cfg.PROJECT}.bls_cpi.cpi_series`
      WHERE series_id = @sid
    )
    SELECT observation_date, value FROM ranked WHERE rn = 1
    ORDER BY observation_date
    """
    return _pull_series(client, sql, cfg.CPI_SERIES_ID)


def _pull_wti_daily(client: bigquery.Client) -> pd.Series:
    sql = f"""
    SELECT observation_date, value
    FROM `{cfg.PROJECT}.eia_petroleum.prices`
    WHERE series_id = @sid
    ORDER BY observation_date
    """
    return _pull_series(client, sql, cfg.EIA_SERIES_ID)


def compute(client: bigquery.Client) -> list[Forecast]:
    panel = pd.DataFrame(
        {
            "sa_idx": _pull_cpi_sa(client),
            "wti": monthly_mean_complete(_pull_wti_daily(client)),
        }
    ).sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    return [
        Forecast(
            target="airfares_cpi_level",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.level,
            value_rounded=round(result.level, 3),  # CPI indexes publish to 3 decimals
            units=cfg.TARGET_UNITS["airfares_cpi_level"],
            n_train=result.n_train,
        ),
        Forecast(
            target="airfares_cpi_mm",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.mm_pct,
            value_rounded=round(result.mm_pct, 1),
            units=cfg.TARGET_UNITS["airfares_cpi_mm"],
            n_train=result.n_train,
        ),
    ]
