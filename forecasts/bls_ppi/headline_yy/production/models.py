"""BigQuery pulls + forecast computation for the headline-PPI production job.

Reads the winning spec's inputs and hands them to the shared model
(``forecasts.bls_ppi.headline_yy.model``):

  * ``bls_ppi.ppi_series``      -- the target, PPI final demand NSA (WPUFD4)
                                   + the SA index (WPSFD4) for the lag-1
                                   regressor (latest vintage per month)
  * ``eia_petroleum.prices``    -- daily Gulf Coast gasoline spot and weekly
                                   retail diesel, sampled at the PPI pricing
                                   date (Tuesday of the week containing the
                                   13th) here
  * ``ism.report_on_business``  -- ISM manufacturing prices paid, month M
                                   (released the 1st business day of M+1,
                                   before the PPI print)

Returns [] (and the job logs + skips) when any target-month regressor is
missing -- in particular, in the days right after a PPI release the next
target month's ISM print has not landed yet, and the job correctly idles
until the 1st of the following month.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery

from forecasts.bls_ppi.headline_yy.data import monthly_at_pricing_date, pull_ism_prices
from forecasts.bls_ppi.headline_yy.model import forecast_next
from forecasts.bls_ppi.headline_yy.production import config as cfg


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


def _pull_ppi(client: bigquery.Client, series_id: str) -> pd.Series:
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, value,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY ingested_at DESC) AS rn
      FROM `{cfg.PROJECT}.bls_ppi.ppi_series`
      WHERE series_id = @sid
    )
    SELECT observation_date, value FROM ranked WHERE rn = 1
    ORDER BY observation_date
    """
    return _pull_series(client, sql, series_id)


def _pull_eia(client: bigquery.Client, series_id: str) -> pd.Series:
    sql = f"""
    SELECT observation_date, value
    FROM `{cfg.PROJECT}.eia_petroleum.prices`
    WHERE series_id = @sid
    ORDER BY observation_date
    """
    return _pull_series(client, sql, series_id)


def compute(client: bigquery.Client) -> list[Forecast]:
    panel = pd.DataFrame(
        {
            "nsa_idx": _pull_ppi(client, cfg.PPI_NSA_ID),
            "sa_idx": _pull_ppi(client, cfg.PPI_SA_ID),
            "ism_mfg": pull_ism_prices(cfg.ISM_REPORT, client=client),
            "gas_mid": monthly_at_pricing_date(_pull_eia(client, cfg.EIA_GAS_SPOT)),
            "diesel_mid": monthly_at_pricing_date(_pull_eia(client, cfg.EIA_DIESEL_WEEKLY)),
        }
    ).sort_index()

    result = forecast_next(panel)
    if result is None:
        return []

    # The published headline is the y/y to 1 decimal; the index publishes to 3.
    targets = [
        ("ppi_fd_yy", result.yy_pct, round(result.yy_pct, 1)),
        ("ppi_fd_mm", result.mm_pct, round(result.mm_pct, 1)),
        ("ppi_fd_level", result.level, round(result.level, 3)),
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
