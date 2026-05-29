"""BigQuery pulls for the ADP national-monthly headline research harness.

Everything here is READ-ONLY. The harness never writes BigQuery (per repo
convention, collectors/forecasts write only from Cloud Run). All series are
returned latest-vintage-per-period because that's all we have today: the ADP
monthly collector has captured a single vintage (2026-05-06) so far, so there
is no point-in-time first-print history yet. We therefore backtest against the
fully-revised SA level as a *proxy* for the as-reported first print, and note
that true first-print history will accrue one month per release going forward.
"""

from __future__ import annotations

import pandas as pd
from google.cloud import bigquery

PROJECT = "us-econ-51920"


def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


def pull_monthly_headline(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Monthly National/U.S. SA employment LEVEL, latest vintage per month.

    Returns columns: month (Timestamp, first-of-month), ner_sa, ner (NSA level).
    The headline is derived downstream as the MoM change in ner_sa.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, ner, ner_sa,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY vintage_date DESC) AS rn
      FROM `{PROJECT}.adp_employment.ner_history`
      WHERE timestep='M' AND aggregation='National' AND category='U.S.'
    )
    SELECT observation_date AS month, ner, ner_sa
    FROM ranked WHERE rn=1
    ORDER BY month
    """
    df = client.query(sql).to_dataframe()
    df["month"] = pd.to_datetime(df["month"])
    return df


def pull_weekly_pulse(client: bigquery.Client | None = None) -> pd.DataFrame:
    """NER Pulse: 4-week MA of net weekly SA private-employment change.

    Latest vintage per week. Columns: week_ending (Timestamp), pulse.
    History starts 2026-01-24 (only ~15 weeks as of 2026-05).
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT week_ending, value,
             ROW_NUMBER() OVER (PARTITION BY week_ending
                                ORDER BY vintage_date DESC) AS rn
      FROM `{PROJECT}.adp_employment.weekly_preliminary`
      WHERE measure='weekly_employment_change_4wk_ma'
    )
    SELECT week_ending, value AS pulse
    FROM ranked WHERE rn=1
    ORDER BY week_ending
    """
    df = client.query(sql).to_dataframe()
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    return df


def pull_claims(client: bigquery.Client | None = None) -> pd.DataFrame:
    """SA initial claims level (the claims-model SA input). Weekly, deep history.

    Columns: week_ending (Timestamp), claims_sa.
    """
    client = client or _client()
    sql = f"""
    SELECT week_ending, value AS claims_sa
    FROM `{PROJECT}.claims.fct_sa_input`
    ORDER BY week_ending
    """
    df = client.query(sql).to_dataframe()
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    return df


def pull_trends(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Google Trends weekly series, wide. Latest non-partial vintage per week.

    Columns: week_ending (Timestamp), then one column per series (prefixed
    ``trends_``). History from 2021-05.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT week_ending, series_id, value,
             ROW_NUMBER() OVER (PARTITION BY series_id, week_ending
                                ORDER BY vintage_date DESC, is_partial ASC) AS rn
      FROM `{PROJECT}.google_trends.weekly`
    )
    SELECT week_ending, series_id, value FROM ranked WHERE rn=1
    """
    long = client.query(sql).to_dataframe()
    long["week_ending"] = pd.to_datetime(long["week_ending"])
    wide = long.pivot(index="week_ending", columns="series_id", values="value")
    wide.columns = [c.replace("trends.us.", "trends_") for c in wide.columns]
    return wide.reset_index().sort_values("week_ending")


def pull_all() -> dict[str, pd.DataFrame]:
    client = _client()
    return {
        "monthly": pull_monthly_headline(client),
        "pulse": pull_weekly_pulse(client),
        "claims": pull_claims(client),
        "trends": pull_trends(client),
    }
