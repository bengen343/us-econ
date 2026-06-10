"""BigQuery pulls + forecast computation for the CPI-gasoline production job.

Reads the two inputs and hands them to the shared model
(``forecasts.bls_cpi.gasoline.model``):

  * ``bls_cpi.cpi_series``     -- the target, CPI gasoline SA index
                                  (latest vintage per month; append-only table)
  * ``eia_petroleum.prices``   -- EIA weekly all-grades retail price (clean
                                  upserted table), aggregated to complete-month
                                  means here

Returns [] (and the job logs + skips) when the target month's weekly retail
prices are not complete yet -- the complete-month guard lives in
``data.monthly_mean_complete``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.bls_cpi.gasoline.data import monthly_mean_complete
from forecasts.bls_cpi.gasoline.model import forecast_next
from forecasts.bls_cpi.gasoline.production import config as cfg


@dataclass(frozen=True)
class Forecast:
    target: str
    target_month: pd.Timestamp
    model_version: str
    value: float
    value_rounded: float
    units: str
    n_train: int


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
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", cfg.CPI_SERIES_ID)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float),
        index=pd.to_datetime(frame["observation_date"]),
    )


def _pull_eia_weekly(client: bigquery.Client) -> pd.Series:
    sql = f"""
    SELECT observation_date, value
    FROM `{cfg.PROJECT}.eia_petroleum.prices`
    WHERE series_id = @sid
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", cfg.EIA_SERIES_ID)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float),
        index=pd.to_datetime(frame["observation_date"]),
    )


def compute(client: bigquery.Client) -> list[Forecast]:
    panel = pd.DataFrame(
        {
            "sa_idx": _pull_cpi_sa(client),
            "eia": monthly_mean_complete(_pull_eia_weekly(client)),
        }
    ).sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    return [
        Forecast(
            target="gas_cpi_level",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.level,
            value_rounded=round(result.level, 3),  # CPI indexes publish to 3 decimals
            units=cfg.TARGET_UNITS["gas_cpi_level"],
            n_train=result.n_train,
        ),
        Forecast(
            target="gas_cpi_mm",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.mm_pct,
            value_rounded=round(result.mm_pct, 1),
            units=cfg.TARGET_UNITS["gas_cpi_mm"],
            n_train=result.n_train,
        ),
    ]
