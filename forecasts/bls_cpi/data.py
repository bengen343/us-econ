"""Shared READ-ONLY BigQuery pulls for the CPI forecasts.

Nothing here writes BigQuery — per repo convention only the deployed Cloud Run
collectors/forecasts write; research harnesses are offline. Every series is
returned latest-vintage-per-period:

  * ``bls_cpi.cpi_series`` carries the published index level plus the BLS-computed
    1-/3-/12-month percent changes per vintage. The published month-over-month
    change is taken from the SA series, the year-over-year from the NSA series
    (they agree at 12 months). NSA indices are effectively final; SA indices are
    re-seasonalised ~annually, so SA m/m backtests are against the revised SA
    level as a proxy for the first print.
  * ``eia_petroleum.prices`` is a clean upserted table (one row per series/date);
    we aggregate the daily/weekly fuel prices to monthly means here.
  * ``bls_cpi.relative_importance`` anchors the bottom-up aggregation weights.
"""

from __future__ import annotations

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

PROJECT = "us-econ-51920"

# CU item codes consumed by the reconstruction, with the short names used as
# column prefixes downstream.
ITEM_SHORT: dict[str, str] = {
    "SA0": "all",  # All items (headline)
    "SA0L1E": "core",  # All items less food and energy
    "SAF1": "food",  # Food
    "SA0E": "energy",  # Energy
    "SETB01": "gas",  # Gasoline (all types)
    "SEHF": "enserv",  # Energy services (electricity + utility gas) -- non-gasoline energy proxy
    "SETA02": "uc",  # Used cars and trucks -- nowcast from the wholesale Manheim index
}


def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


def pull_cpi(client: bigquery.Client | None = None) -> pd.DataFrame:
    """CPI items wide, latest vintage per (series_id, month).

    Returns a frame indexed by ``month`` with, for each item in ITEM_SHORT and
    each adjustment, columns ``{short}_{sa|nsa}_{idx|mm|yy}`` where idx is the
    index level, mm the published 1-month % change, yy the published 12-month %
    change.
    """
    client = client or _client()
    codes = ", ".join(f"'{c}'" for c in ITEM_SHORT)
    sql = f"""
    WITH ranked AS (
      SELECT item_code, seasonally_adjusted, observation_date,
             value, pct_change_1m, pct_change_12m,
             ROW_NUMBER() OVER (PARTITION BY series_id, observation_date
                                ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.bls_cpi.cpi_series`
      WHERE item_code IN ({codes})
    )
    SELECT item_code, seasonally_adjusted, observation_date, value, pct_change_1m, pct_change_12m
    FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["observation_date"] = pd.to_datetime(long["observation_date"])
    long["base"] = long["item_code"].map(ITEM_SHORT) + long["seasonally_adjusted"].map(
        {True: "_sa", False: "_nsa"}
    )

    metrics = {"idx": "value", "mm": "pct_change_1m", "yy": "pct_change_12m"}
    frames = []
    for suffix, col in metrics.items():
        wide = long.pivot(index="observation_date", columns="base", values=col)
        wide.columns = [f"{c}_{suffix}" for c in wide.columns]
        frames.append(wide)
    out = pd.concat(frames, axis=1)
    out.index.name = "month"
    return out.sort_index().reset_index()


def pull_eia_monthly(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Monthly-mean fuel prices. Columns: month, gas_price (all-grades retail,
    $/gal), wti (Cushing spot, $/bbl). The CPI gasoline index reflects the
    calendar-month average pump price, so the monthly mean is the right grain."""
    client = client or _client()
    sql = f"""
    SELECT DATE_TRUNC(observation_date, MONTH) AS month, series_id, AVG(value) AS price
    FROM `{PROJECT}.eia_petroleum.prices`
    WHERE series_id IN ('EMM_EPM0_PTE_NUS_DPG', 'RWTC')
    GROUP BY month, series_id
    """
    long = client.query(sql).to_dataframe()
    long["month"] = pd.to_datetime(long["month"])
    wide = long.pivot(index="month", columns="series_id", values="price").rename(
        columns={"EMM_EPM0_PTE_NUS_DPG": "gas_price", "RWTC": "wti"}
    )
    return wide.sort_index().reset_index()


def pull_manheim(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Manheim Used Vehicle Value Index (SA, 1997-01 = 100), monthly, latest
    vintage per month. Columns: month, manheim_sa.

    Wholesale used-vehicle prices lead CPI used cars & trucks by ~1-2 months, and
    month M's full value is published on the 5th business day of M+1 -- before
    M's CPI release, so it is a fully-observed regressor at the nowcast origin.

    Returns an empty frame if the dataset hasn't been provisioned/seeded yet, so
    the reconstruction degrades to its no-used-cars form instead of crashing.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT observation_month, value,
             ROW_NUMBER() OVER (PARTITION BY observation_month
                                ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.manheim_used_vehicles.value_index`
      WHERE measure = 'index_sa'
    )
    SELECT observation_month AS month, value AS manheim_sa
    FROM ranked WHERE rn = 1
    ORDER BY month
    """
    try:
        df = client.query(sql).to_dataframe()
    except NotFound:
        return pd.DataFrame(columns=["month", "manheim_sa"])
    df["month"] = pd.to_datetime(df["month"])
    return df


def pull_cpi_weights(client: bigquery.Client | None = None) -> tuple[dict[str, float], int]:
    """CPI-U relative importances (percent of all items) for the latest weight
    year. Returns ``(weights_by_item_code, weight_year)``. The weight year locates
    the December reference month used to price-update the weights to each period.
    """
    client = client or _client()
    sql = f"""
    SELECT item_code, relative_importance, weight_year
    FROM `{PROJECT}.bls_cpi.relative_importance`
    WHERE population = 'CPI-U'
      AND weight_year = (SELECT MAX(weight_year) FROM `{PROJECT}.bls_cpi.relative_importance`)
    """
    df = client.query(sql).to_dataframe()
    weights = dict(zip(df["item_code"], df["relative_importance"], strict=False))
    return weights, int(df["weight_year"].iloc[0])
