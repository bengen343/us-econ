"""Shared READ-ONLY BigQuery pulls for the AAA gasoline next-day forecast.

Nothing here writes BigQuery -- per repo convention only the deployed Cloud Run
collectors/forecasts write; research harnesses are offline.

The forecast target is the daily AAA national average regular retail price
(``aaa_gasoline.daily``), but that series only began in 2026-05, so the research
harness validates methodology on its long-history analog -- EIA's weekly U.S.
regular retail gasoline (``EMM_EPMR_PTE_NUS_DPG``, 2000-present), the same
quantity sampled weekly. Upstream daily market signals come from
``energy_futures.daily`` (RBOB RB=F, WTI CL=F) and ``eia_petroleum`` (gasoline
spot + crude spot + weekly supply fundamentals).
"""

from __future__ import annotations

import pandas as pd
from google.cloud import bigquery

PROJECT = "us-econ-51920"

# EIA weekly U.S. regular retail gasoline -- the long-history analog of AAA.
EIA_RETAIL_REGULAR = "EMM_EPMR_PTE_NUS_DPG"
# Daily U.S. gasoline spot (wholesale-level); NY Harbor conventional regular.
EIA_GAS_SPOT_NY = "EER_EPMRU_PF4_Y35NY_DPG"


def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


def pull_aaa_daily(client: bigquery.Client | None = None) -> pd.DataFrame:
    """AAA daily national-average prices by grade, latest vintage per (date, grade).

    aaa_gasoline.daily is append-mode (no upsert), so dedupe to the newest
    ingestion per (observation_date, grade). Returns columns: observation_date,
    grade, price.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, grade, price_usd_per_gallon AS price,
             ROW_NUMBER() OVER (PARTITION BY observation_date, grade
                                ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.aaa_gasoline.daily`
    )
    SELECT observation_date, grade, price
    FROM ranked WHERE rn = 1
    ORDER BY observation_date, grade
    """
    df = client.query(sql).to_dataframe()
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    return df


def pull_aaa_regular(client: bigquery.Client | None = None) -> pd.Series:
    """AAA daily national-average REGULAR price as a date-indexed Series ($/gal)."""
    df = pull_aaa_daily(client)
    reg = df[df["grade"] == "Regular"].set_index("observation_date")["price"].sort_index()
    reg.name = "aaa_regular"
    return reg


def pull_eia_retail_weekly(client: bigquery.Client | None = None) -> pd.Series:
    """EIA weekly U.S. regular retail gasoline ($/gal), date-indexed (Mondays).

    The upserted eia_petroleum.prices is one-row-per-series/date, so no vintage
    dedupe is needed.
    """
    client = client or _client()
    sql = f"""
    SELECT observation_date, value
    FROM `{PROJECT}.eia_petroleum.prices`
    WHERE series_id = '{EIA_RETAIL_REGULAR}'
    ORDER BY observation_date
    """
    df = client.query(sql).to_dataframe()
    s = pd.Series(
        df["value"].to_numpy(),
        index=pd.to_datetime(df["observation_date"]),
        name="eia_retail_regular",
    )
    return s.sort_index()


def pull_futures_daily(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Daily futures settles, wide by ticker. Columns: rbob (RB=F), wti (CL=F),
    brent (BZ=F), date-indexed ($/gal for rbob, $/bbl for crude)."""
    client = client or _client()
    sql = f"""
    SELECT observation_date, ticker, close
    FROM `{PROJECT}.energy_futures.daily`
    WHERE ticker IN ('RB=F', 'CL=F', 'BZ=F')
    ORDER BY observation_date
    """
    df = client.query(sql).to_dataframe()
    wide = df.pivot(index="observation_date", columns="ticker", values="close").rename(
        columns={"RB=F": "rbob", "CL=F": "wti", "BZ=F": "brent"}
    )
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    return wide.sort_index()


def pull_eia_spot_daily(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Daily EIA spot prices, wide. Columns: gas_spot_ny (NY Harbor conventional
    regular, $/gal), wti_spot (RWTC), brent_spot (RBRTE), date-indexed."""
    client = client or _client()
    sql = f"""
    SELECT observation_date, series_id, value
    FROM `{PROJECT}.eia_petroleum.prices`
    WHERE series_id IN ('{EIA_GAS_SPOT_NY}', 'RWTC', 'RBRTE')
    ORDER BY observation_date
    """
    df = client.query(sql).to_dataframe()
    wide = df.pivot(index="observation_date", columns="series_id", values="value").rename(
        columns={EIA_GAS_SPOT_NY: "gas_spot_ny", "RWTC": "wti_spot", "RBRTE": "brent_spot"}
    )
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    return wide.sort_index()


def pull_supply_weekly(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Weekly EIA supply fundamentals, wide. Columns: gas_stocks (total motor
    gasoline ending stocks, MBBL), refinery_util (% operable capacity),
    date-indexed."""
    client = client or _client()
    sql = f"""
    SELECT observation_date, series_id, value
    FROM `{PROJECT}.eia_petroleum.supply`
    WHERE series_id IN ('WGTSTUS1', 'WPULEUS3')
    ORDER BY observation_date
    """
    df = client.query(sql).to_dataframe()
    wide = df.pivot(index="observation_date", columns="series_id", values="value").rename(
        columns={"WGTSTUS1": "gas_stocks", "WPULEUS3": "refinery_util"}
    )
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    return wide.sort_index()
