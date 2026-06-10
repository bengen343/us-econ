"""BigQuery pull + forecast computation for the electricity-price production job.

Single input: the average price of electricity from ``bls_cpi.average_prices``
(latest vintage per month; append-only table), handed to the shared model
(``forecasts.bls_cpi.electricity.model``). Returns [] (and the job logs +
skips) when the history is too short or the target month's own-lag features
are unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.bls_cpi.electricity.model import forecast_next
from forecasts.bls_cpi.electricity.production import config as cfg


@dataclass(frozen=True)
class Forecast:
    target: str
    target_month: pd.Timestamp
    model_version: str
    value: float
    value_rounded: float
    units: str
    n_train: int


def _pull_ap(client: bigquery.Client) -> pd.Series:
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, value,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY ingested_at DESC) AS rn
      FROM `{cfg.PROJECT}.bls_cpi.average_prices`
      WHERE series_id = @sid
    )
    SELECT observation_date, value FROM ranked WHERE rn = 1
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", cfg.AP_SERIES_ID)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float),
        index=pd.to_datetime(frame["observation_date"]),
    )


def compute(client: bigquery.Client) -> list[Forecast]:
    panel = pd.DataFrame({"ap": _pull_ap(client)}).sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    return [
        Forecast(
            target="electricity_ap_level",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.level,
            value_rounded=round(result.level, 3),  # AP is published to 3 decimals
            units=cfg.TARGET_UNITS["electricity_ap_level"],
            n_train=result.n_train,
        ),
        Forecast(
            target="electricity_ap_mm",
            target_month=result.target_month,
            model_version=cfg.MODEL_VERSION,
            value=result.mm_pct,
            value_rounded=round(result.mm_pct, 1),
            units=cfg.TARGET_UNITS["electricity_ap_mm"],
            n_train=result.n_train,
        ),
    ]
