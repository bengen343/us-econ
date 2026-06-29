"""Data layer for the Challenger job-cuts headline forecast.

Builds a monthly panel indexed by ``observation_month`` (first of month):

  * ``y``            — headline announced job cuts (layoffs, total)
  * leading-indicator features, each aligned to the *same* month being forecast
    and all observable before the report's ~first-Thursday release of month M+1.

The headline target comes from BigQuery (``challenger_employment.monthly``), which
holds the live collector history plus the one-off Wayback backfill (monthly
headline reconstructed back to 2012). All indicator pulls aggregate
higher-frequency series to a monthly value for the report month.
"""

from __future__ import annotations

import logging

import pandas as pd

_log = logging.getLogger(__name__)

# COVID months to mask in research scoring (announcement series exploded then).
COVID_START = pd.Timestamp("2020-03-01")
COVID_END = pd.Timestamp("2021-06-01")


# ---------- target ----------

def load_headline(client) -> pd.Series:
    """Monthly headline (layoffs total) from BigQuery, indexed by month start."""
    sql = """
        SELECT observation_month, value
        FROM `challenger_employment.monthly`
        WHERE series = 'layoffs' AND breakdown = 'total'
    """
    df = client.query(sql).result().to_dataframe()
    s = pd.Series(df["value"].values, index=pd.to_datetime(df["observation_month"]), name="y")
    s = s.sort_index()
    s.index = s.index.to_period("M").to_timestamp()  # normalize to month start
    return s[~s.index.duplicated(keep="first")].sort_index()


# ---------- indicator pulls (each returns a monthly Series indexed by month start) ----------

def _monthly(idx_dates: pd.Series, values: pd.Series, how: str = "mean") -> pd.Series:
    df = pd.DataFrame({"d": pd.to_datetime(idx_dates), "v": values})
    df["m"] = df["d"].dt.to_period("M").dt.to_timestamp()
    g = df.groupby("m")["v"]
    out = g.mean() if how == "mean" else g.sum()
    return out.sort_index()


def pull_initial_claims(client) -> pd.Series:
    """Monthly mean of weekly national NSA initial claims (weeks ending in month)."""
    sql = """
        SELECT week_ending, value
        FROM `claims.weekly_claims`
        WHERE level = 'national' AND measure = 'initial_claims'
          AND seasonal_adjustment = 'nsa'
    """
    df = client.query(sql).result().to_dataframe()
    return _monthly(df["week_ending"], df["value"], "mean").rename("claims_nsa")


def pull_ism_employment(client) -> pd.Series:
    """ISM manufacturing employment diffusion index, monthly."""
    sql = """
        SELECT observation_month, value
        FROM `ism.report_on_business`
        WHERE report = 'manufacturing' AND measure = 'employment'
    """
    df = client.query(sql).result().to_dataframe()
    s = pd.Series(df["value"].values, index=pd.to_datetime(df["observation_month"]))
    s.index = s.index.to_period("M").to_timestamp()
    return s[~s.index.duplicated()].sort_index().rename("ism_emp")


def pull_conf_board(client) -> pd.Series:
    """Conference Board labor differential (jobs-plentiful minus jobs-hard-to-get)."""
    sql = """
        SELECT observation_month, value
        FROM `conference_board.consumer_confidence`
        WHERE measure = 'labor_differential'
    """
    df = client.query(sql).result().to_dataframe()
    s = pd.Series(df["value"].values, index=pd.to_datetime(df["observation_month"]))
    s.index = s.index.to_period("M").to_timestamp()
    return s[~s.index.duplicated()].sort_index().rename("cb_labor_differential")


def pull_michigan(client) -> pd.Series:
    """University of Michigan consumer sentiment (final), monthly."""
    sql = """
        SELECT observation_month, value
        FROM `michigan_sentiment.surveys_of_consumers`
        WHERE measure = 'sentiment' AND release_type = 'final'
    """
    df = client.query(sql).result().to_dataframe()
    s = pd.Series(df["value"].values, index=pd.to_datetime(df["observation_month"]))
    s.index = s.index.to_period("M").to_timestamp()
    return s[~s.index.duplicated()].sort_index().rename("mich")


def build_panel(client) -> pd.DataFrame:
    """Assemble the monthly panel: target ``y`` plus the model's indicators."""
    y = load_headline(client)
    feats = [
        pull_initial_claims(client),
        pull_ism_employment(client),
        pull_conf_board(client),
        pull_michigan(client),
    ]
    panel = pd.DataFrame({"y": y})
    for f in feats:
        panel = panel.join(f, how="outer")
    panel = panel.sort_index()
    panel.index.name = "observation_month"
    return panel
