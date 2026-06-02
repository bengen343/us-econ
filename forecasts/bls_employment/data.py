"""Shared READ-ONLY BigQuery pulls for the BLS Employment Situation forecasts.

Two separate research harnesses live alongside this module
(``payrolls_headline`` and ``unemployment_rate``); they keep their own panels
and walk-forward harnesses but share this single read-only data layer so the
SQL isn't duplicated. Nothing here writes BigQuery — per repo convention only
the Cloud Run collectors/forecasts write, and the research harnesses are
offline.

Every series is returned latest-vintage-per-period, because that is all the
history supports today:

  * ``bls_employment.employment_situation`` has only 2 ingest vintages so far
    (the 2026-05-06 backfill + the 2026-05-29 year-cap fix), so there is no
    real first-print PIT history. We therefore backtest against the revised
    level as a *proxy* for the as-reported first print, and note that true
    first prints accrue one per monthly release going forward. NFP first-print
    revisions are large, so backtest error is optimistic vs the true objective.
  * Weekly claims carry a real ``vintage_date``; we take the freshest vintage
    per week (which surfaces the dol_press_pdf rows for the most recent weeks
    and doleta_xml for the deep history — they agree where they overlap).
"""

from __future__ import annotations

import pandas as pd
from google.cloud import bigquery

PROJECT = "us-econ-51920"


def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


# --------------------------------------------------------------------------- #
# BLS Employment Situation (targets + monthly feature components)
# --------------------------------------------------------------------------- #
def pull_bls_series(series_ids: list[str], client: bigquery.Client | None = None) -> pd.DataFrame:
    """Monthly BLS series, wide, latest vintage per (series_id, month).

    Returns a frame indexed by ``month`` (first-of-month Timestamp) with one
    column per requested ``series_id`` holding ``value`` (SA level/rate as
    published). ``series_id`` already encodes seasonal adjustment (CES*/LNS* are
    SA, CEU*/LNU* are NSA), so no extra filter is needed.
    """
    client = client or _client()
    id_list = ", ".join(f"'{s}'" for s in series_ids)
    sql = f"""
    WITH ranked AS (
      SELECT series_id, observation_date, value,
             ROW_NUMBER() OVER (PARTITION BY series_id, observation_date
                                ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.bls_employment.employment_situation`
      WHERE series_id IN ({id_list})
    )
    SELECT series_id, observation_date, value FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["observation_date"] = pd.to_datetime(long["observation_date"])
    wide = long.pivot(index="observation_date", columns="series_id", values="value")
    wide.index.name = "month"
    return wide.sort_index().reset_index()


# --------------------------------------------------------------------------- #
# National claims (initial / continued / insured-unemployment-rate, SA)
# --------------------------------------------------------------------------- #
def pull_claims_national(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Freshest-vintage national SA claims, weekly. Columns:

      week_ending, claims_initial_sa, claims_continued_sa, iur_sa

    ``iur_sa`` is the insured unemployment rate (percent) — a direct weekly
    coincident analogue of the headline unemployment rate. Deep history from
    doleta_xml (2006+) with dol_press_pdf overlaid for the most recent weeks.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT week_ending, measure, value,
             ROW_NUMBER() OVER (PARTITION BY measure, week_ending
                                ORDER BY vintage_date DESC) AS rn
      FROM `{PROJECT}.claims.weekly_claims`
      WHERE level = 'national' AND area = 'US' AND seasonal_adjustment = 'sa'
        AND measure IN ('initial_claims', 'continued_claims', 'iur')
    )
    SELECT week_ending, measure, value FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["week_ending"] = pd.to_datetime(long["week_ending"])
    wide = long.pivot(index="week_ending", columns="measure", values="value")
    wide = wide.rename(
        columns={
            "initial_claims": "claims_initial_sa",
            "continued_claims": "claims_continued_sa",
            "iur": "iur_sa",
        }
    )
    return wide.sort_index().reset_index()


# --------------------------------------------------------------------------- #
# ADP National Employment Report (monthly headline + weekly Pulse)
# --------------------------------------------------------------------------- #
def pull_adp_monthly(client: bigquery.Client | None = None) -> pd.DataFrame:
    """ADP monthly National/U.S. SA employment LEVEL, latest vintage per month.

    Columns: month, adp_sa (SA level, thousands). The ADP monthly headline =
    its MoM change; released the first Wednesday of M+1, ~2 days before NFP, so
    it is knowable at the NFP forecast origin.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT observation_date, ner_sa,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY vintage_date DESC) AS rn
      FROM `{PROJECT}.adp_employment.ner_history`
      WHERE timestep = 'M' AND aggregation = 'National' AND category = 'U.S.'
    )
    SELECT observation_date AS month, ner_sa AS adp_sa FROM ranked WHERE rn = 1
    ORDER BY month
    """
    df = client.query(sql).to_dataframe()
    df["month"] = pd.to_datetime(df["month"])
    return df


def pull_adp_pulse(client: bigquery.Client | None = None) -> pd.DataFrame:
    """ADP NER Pulse: 4-week MA of net weekly SA private-employment change.

    Columns: week_ending, pulse. Data-starved (history from 2026-01).
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT week_ending, value,
             ROW_NUMBER() OVER (PARTITION BY week_ending
                                ORDER BY vintage_date DESC) AS rn
      FROM `{PROJECT}.adp_employment.weekly_preliminary`
      WHERE measure = 'weekly_employment_change_4wk_ma'
    )
    SELECT week_ending, value AS pulse FROM ranked WHERE rn = 1
    ORDER BY week_ending
    """
    df = client.query(sql).to_dataframe()
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    return df


# --------------------------------------------------------------------------- #
# Challenger job-cut / hiring announcements (monthly)
# --------------------------------------------------------------------------- #
def pull_challenger(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Challenger national monthly announced job cuts and hiring plans.

    Columns: month, challenger_layoffs, challenger_hiring. Released the first
    Thursday of M+1 (knowable at the NFP origin), BUT the layoffs total has only
    ~15 months of history here, so it is a recent-only signal; hiring goes back
    to 2017. Latest vintage per month.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT series, observation_month, value,
             ROW_NUMBER() OVER (PARTITION BY series, observation_month
                                ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.challenger_employment.monthly`
      WHERE breakdown = 'total' AND series IN ('layoffs', 'hiring')
    )
    SELECT series, observation_month, value FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["observation_month"] = pd.to_datetime(long["observation_month"])
    wide = long.pivot(index="observation_month", columns="series", values="value")
    wide = wide.rename(columns={"layoffs": "challenger_layoffs", "hiring": "challenger_hiring"})
    wide.index.name = "month"
    return wide.sort_index().reset_index()


# --------------------------------------------------------------------------- #
# Google Trends (weekly search interest)
# --------------------------------------------------------------------------- #
def pull_trends(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Google Trends weekly series, wide, latest non-partial vintage per week.

    Columns: week_ending, then one ``trends_*`` column per series. History from
    2021-05. Week_ending is Saturday-aligned to match the claims calendar.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT week_ending, series_id, value,
             ROW_NUMBER() OVER (PARTITION BY series_id, week_ending
                                ORDER BY vintage_date DESC, is_partial ASC) AS rn
      FROM `{PROJECT}.google_trends.weekly`
    )
    SELECT week_ending, series_id, value FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["week_ending"] = pd.to_datetime(long["week_ending"])
    wide = long.pivot(index="week_ending", columns="series_id", values="value")
    wide.columns = [c.replace("trends.us.", "trends_") for c in wide.columns]
    return wide.reset_index().sort_values("week_ending")


# --------------------------------------------------------------------------- #
# ISM Report On Business (Manufacturing + Services diffusion indexes)
# --------------------------------------------------------------------------- #
def pull_ism(client: bigquery.Client | None = None) -> pd.DataFrame:
    """ISM Manufacturing + Services indexes, monthly, wide. Latest vintage per
    (report, measure, observation_month).

    Columns: month, then ``ism_mfg_*`` / ``ism_svc_*`` for the headline ``pmi``
    and ``employment`` indexes. Released early in M+1 (Manufacturing 1st business
    day, Services 3rd) — ahead of the Employment Situation, so month M is the
    timely contemporaneous nowcast input.
    """
    client = client or _client()
    sql = f"""
    WITH ranked AS (
      SELECT report, measure, observation_month, value,
             ROW_NUMBER() OVER (PARTITION BY report, measure, observation_month
                                ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.ism.report_on_business`
      WHERE measure IN ('pmi', 'employment')
    )
    SELECT report, measure, observation_month, value FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["observation_month"] = pd.to_datetime(long["observation_month"])
    rpt = long["report"].map({"manufacturing": "mfg", "services": "svc"})
    long["col"] = "ism_" + rpt + "_" + long["measure"]
    wide = long.pivot(index="observation_month", columns="col", values="value")
    wide.index.name = "month"
    return wide.sort_index().reset_index()


# --------------------------------------------------------------------------- #
# Conference Board Consumer Confidence (survey shares + indexes)
# --------------------------------------------------------------------------- #
def pull_conference_board(client: bigquery.Client | None = None) -> pd.DataFrame:
    """Conference Board labor-relevant series, monthly, wide. Latest vintage per
    (measure, observation_month).

    Columns: month, then ``cb_*`` for the labor differential, the jobs shares,
    and the six-months-ahead jobs expectations. Released ~last Tuesday of month
    M — ahead of the Employment Situation, so month M is contemporaneous.
    """
    client = client or _client()
    wanted = [
        "labor_differential", "jobs_plentiful", "jobs_hard_to_get",
        "exp_jobs_more", "exp_jobs_fewer",
    ]
    id_list = ", ".join(f"'{m}'" for m in wanted)
    sql = f"""
    WITH ranked AS (
      SELECT measure, observation_month, value,
             ROW_NUMBER() OVER (PARTITION BY measure, observation_month
                                ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.conference_board.consumer_confidence`
      WHERE measure IN ({id_list})
    )
    SELECT measure, observation_month, value FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["observation_month"] = pd.to_datetime(long["observation_month"])
    wide = long.pivot(index="observation_month", columns="measure", values="value")
    wide.columns = [f"cb_{c}" for c in wide.columns]
    wide.index.name = "month"
    return wide.sort_index().reset_index()
