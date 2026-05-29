"""Read-only, vintage-aware BigQuery pulls for the Pulse-bridge forecast.

Both pulls accept an ``as_of`` date so the backtest can reconstruct the
information set of a past Tuesday; production passes ``as_of=None`` (today),
which simply takes the freshest vintage of everything.

Point-in-time honesty caveats (same as the claims work):
  * Only ONE monthly vintage (2026-05-06) exists so far, so monthly history is
    effectively already-revised. ``released_by`` still filters to headlines that
    would have been *public* by ``as_of`` (first Wednesday of M+1), which is the
    part that matters for picking the live target month.
  * The Pulse has 2 vintages; we approximate finer as-of granularity by also
    dropping weeks whose week_ending is newer than ``as_of - PULSE_LAG_DAYS``
    (i.e. not yet published on that date).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from google.cloud import bigquery

from forecasts.adp_employment.national_monthly.pulse_bridge.config import (
    PROJECT,
    PULSE_LAG_DAYS,
)


def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


def first_wednesday(year: int, month: int) -> date:
    """First Wednesday of a month — when the prior month's headline is released."""
    d = date(year, month, 1)
    # weekday(): Mon=0 .. Wed=2
    return d + pd.Timedelta(days=(2 - d.weekday()) % 7)


def release_date_for(month: pd.Timestamp) -> date:
    """Release date of month M's headline = first Wednesday of M+1."""
    nxt = (month + pd.offsets.MonthBegin(1)).date()
    return first_wednesday(nxt.year, nxt.month)


def pull_monthly(client: bigquery.Client | None = None,
                 as_of: date | None = None) -> pd.DataFrame:
    """Monthly National/U.S. SA level, freshest vintage at/<= as_of, restricted
    to headlines released by as_of. Columns: month (Timestamp), ner_sa."""
    client = client or _client()
    conds = ["timestep='M'", "aggregation='National'", "category='U.S.'"]
    if as_of is not None:
        conds.append(f"vintage_date <= DATE '{as_of.isoformat()}'")
    where = "WHERE " + " AND ".join(conds)
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, ner_sa,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY vintage_date DESC) AS rn
      FROM `{PROJECT}.adp_employment.ner_history`
      {where}
    )
    SELECT observation_date AS month, ner_sa
    FROM ranked WHERE rn=1
    ORDER BY month
    """
    df = client.query(sql).to_dataframe()
    df["month"] = pd.to_datetime(df["month"])
    if as_of is not None:
        released = df["month"].apply(lambda m: release_date_for(m) <= as_of)
        df = df[released].reset_index(drop=True)
    return df


def pull_pulse(client: bigquery.Client | None = None,
               as_of: date | None = None) -> pd.DataFrame:
    """Weekly NER Pulse (SA 4-wk MA change), freshest vintage at/<= as_of,
    restricted to weeks published by as_of. Columns: week_ending (Timestamp), pulse."""
    client = client or _client()
    conds = []
    if as_of is not None:
        conds.append(f"vintage_date <= DATE '{as_of.isoformat()}'")
    where_v = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
    WITH ranked AS (
      SELECT week_ending, value,
             ROW_NUMBER() OVER (PARTITION BY week_ending
                                ORDER BY vintage_date DESC) AS rn
      FROM `{PROJECT}.adp_employment.weekly_preliminary`
      {where_v}
    )
    SELECT week_ending, value AS pulse
    FROM ranked WHERE rn=1
    ORDER BY week_ending
    """
    df = client.query(sql).to_dataframe()
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    if as_of is not None:
        published = pd.Timestamp(as_of) - pd.Timedelta(days=PULSE_LAG_DAYS)
        df = df[df["week_ending"] <= published].reset_index(drop=True)
    return df
