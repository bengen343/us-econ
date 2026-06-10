"""BigQuery pull + forecast computation for the CPI-shelter production job.

Single input: the CPI shelter SA index from ``bls_cpi.cpi_series`` (latest
vintage per month; append-only table), handed to the shared deterministic
model (``forecasts.bls_cpi.shelter.model``). Returns [] (and the job logs +
skips) when fewer than 4 of the trailing 6 m/m changes are published.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.bls_cpi.shelter.model import forecast_next
from forecasts.bls_cpi.shelter.production import config as cfg


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


def compute(client: bigquery.Client) -> list[Forecast]:
    panel = pd.DataFrame({"sh_idx": _pull_cpi_sa(client)}).sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    return [
        Forecast(
            target="shelter_cpi_level",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.level,
            value_rounded=round(result.level, 3),  # CPI indexes publish to 3 decimals
            units=cfg.TARGET_UNITS["shelter_cpi_level"],
            n_train=result.n_train,
        ),
        Forecast(
            target="shelter_cpi_mm",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.mm_pct,
            value_rounded=round(result.mm_pct, 1),
            units=cfg.TARGET_UNITS["shelter_cpi_mm"],
            n_train=result.n_train,
        ),
    ]
