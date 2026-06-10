"""Data pulls for the Michigan sentiment forecasts (research harness +
production).

Two targets, released on alternating Fridays (so exactly one is pending at
any time):

  * **Preliminary** ICS for month M, ~2nd Friday of M. At the origin the
    newest Michigan print is the M-1 final; the survey's prelim interviews
    run from late M-1 into the first ~week of M, so the forecastable signal
    is what those respondents experienced: gasoline prices, stocks, and the
    news flow over late M-1 / early M.
  * **Final** ICS for month M, ~4th Friday of M. The prelim for M is
    published and the final's sample *includes* the prelim interviews
    (prelim<->final correlation ~0.97), so the target is the REVISION, driven
    by what changed after the prelim window (mid-month news).

Inputs and point-in-time status at each origin:

  * ``michigan_sentiment.surveys_of_consumers`` (BigQuery): prelim + final
    history (prelim from 1997-06).
  * Gasoline: EIA Gulf Coast conventional spot (daily, 2000+) and U.S. retail
    all-grades (weekly Mondays), both in ``eia_petroleum.prices`` -- partial
    early/mid-month windows are fully observed at both origins.
  * S&P 500 (^GSPC, Yahoo chart API, daily): same-day availability.
  * SF Fed Daily News Sentiment Index (xlsx, daily 1980+): updated weekly
    with a few days' lag -- documented UMich-sentiment predictor. Windows
    ending ~day 5 of M are usable at the prelim origin in most weeks.
  * ``conference_board.consumer_confidence`` (BigQuery): CB confidence for
    M-1 (released the last Tuesday of M-1) is safe at the prelim origin; the
    month-M CB release can land before OR after the Michigan final, so the
    revision spec only uses lag 1.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

PROJECT = "us-econ-51920"

EIA_GAS_SPOT = "EER_EPMRU_PF4_RGC_DPG"  # conventional regular, Gulf Coast, daily
EIA_GAS_RETAIL = "EMM_EPM0_PTE_NUS_DPG"  # all grades retail, weekly Mondays
SP500_TICKER = "^GSPC"
DNSI_URL = "https://www.frbsf.org/wp-content/uploads/news_sentiment_data.xlsx"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
# Yahoo rejects the default collector UA; send a browser UA (same workaround
# as collectors/energy_futures).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)


def month_window_mean(obs: pd.Series, first_day: int, last_day: int) -> pd.Series:
    """Per-month mean of a daily/weekly series over days [first_day, last_day]
    of the month. Partial-month live windows fall out naturally."""
    obs = obs.dropna()
    sel = obs[(obs.index.day >= first_day) & (obs.index.day <= last_day)]
    frame = sel.to_frame("value")
    frame["month"] = frame.index.to_period("M").to_timestamp()
    return frame.groupby("month")["value"].mean()


def survey_window_mean(obs: pd.Series, start_day_prev: int = 25, end_day_cur: int = 7) -> pd.Series:
    """Per-month mean over the prelim interview window: day >= start_day_prev
    of the PRIOR month through day <= end_day_cur of the month itself --
    aligned to when Michigan's preliminary respondents are actually surveyed."""
    obs = obs.dropna()
    month = obs.index.to_period("M").to_timestamp()
    tail = obs[obs.index.day >= start_day_prev]
    head = obs[obs.index.day <= end_day_cur]
    pieces = pd.concat(
        [
            pd.DataFrame({"value": tail, "month": tail.index.to_period("M").to_timestamp()}).assign(
                month=lambda f: f["month"] + pd.offsets.MonthBegin(1)
            ),
            pd.DataFrame({"value": head, "month": month[obs.index.day <= end_day_cur]}),
        ]
    )
    return pieces.groupby("month")["value"].mean()


def month_mean(obs: pd.Series) -> pd.Series:
    """Full-calendar-month means (no completeness guard: used for lagged,
    fully-elapsed months only)."""
    obs = obs.dropna()
    frame = obs.to_frame("value")
    frame["month"] = frame.index.to_period("M").to_timestamp()
    return frame.groupby("month")["value"].mean()


def pull_michigan(client=None) -> pd.DataFrame:
    """Monthly frame with ``prelim`` and ``final`` ICS columns."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, release_type, value
    FROM `{PROJECT}.michigan_sentiment.surveys_of_consumers`
    WHERE measure = 'sentiment'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY observation_month, release_type ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    wide = frame.pivot(index="observation_month", columns="release_type", values="value")
    wide = wide.rename(columns={"preliminary": "prelim"})[["prelim", "final"]]
    wide.index.name = "month"
    return wide


def pull_cb_confidence(client=None) -> pd.Series:
    """Conference Board confidence index, latest vintage per month."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, value
    FROM `{PROJECT}.conference_board.consumer_confidence`
    WHERE measure = 'confidence_index'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY observation_month ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_month"])
    )


def pull_eia_series(series_id: str, client=None) -> pd.Series:
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_date, value
    FROM `{PROJECT}.eia_petroleum.prices`
    WHERE series_id = @sid
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", series_id)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )


def pull_sp500_bq(client=None) -> pd.Series:
    """S&P 500 daily closes from BigQuery (collectors/market_indexes; the
    production path -- the harness pulls Yahoo directly)."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_date, close
    FROM `{PROJECT}.market_indexes.daily`
    WHERE ticker = @ticker
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", SP500_TICKER)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["close"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )


def pull_sp500() -> pd.Series:
    """S&P 500 daily closes from the Yahoo chart API (explicit period bounds:
    range=max silently downsamples to monthly)."""
    from datetime import UTC, datetime

    from collectors.common.http import client, with_retries

    params = {
        "period1": 852076800,  # 1997-01-01 (prelim history starts 1997-06)
        "period2": int(datetime.now(UTC).timestamp()) + 86400,
        "interval": "1d",
    }
    with client() as http:

        def call() -> dict:
            response = http.get(
                YAHOO_CHART.format(ticker=SP500_TICKER),
                params=params,
                headers={"User-Agent": BROWSER_UA},
            )
            response.raise_for_status()
            return response.json()

        body = with_retries(call)
    result = body["chart"]["result"][0]
    stamps = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert("America/New_York")
    closes = result["indicators"]["quote"][0]["close"]
    return (
        pd.Series(closes, index=stamps.normalize().tz_localize(None), dtype=float)
        .dropna()
        .groupby(level=0)
        .last()
    )


def pull_dnsi() -> pd.Series:
    """SF Fed Daily News Sentiment Index (daily, 1980+)."""
    from collectors.common.http import client, with_retries

    with client() as http:

        def call() -> bytes:
            response = http.get(DNSI_URL, headers={"User-Agent": BROWSER_UA})
            response.raise_for_status()
            return response.content

        content = with_retries(call)
    frame = pd.read_excel(io.BytesIO(content), sheet_name="Data")
    return pd.Series(
        frame["News Sentiment"].to_numpy(dtype=float), index=pd.to_datetime(frame["date"])
    ).sort_index()


def pull_panel(cache: str | Path | None = None) -> dict[str, pd.Series | pd.DataFrame]:
    """All raw inputs for the harness, cached as one CSV per source."""
    if cache is not None and Path(cache).exists():
        stored = pd.read_csv(cache, index_col=0, parse_dates=True)
        return {
            "michigan": stored[["prelim", "final"]].dropna(how="all"),
            "cb": stored["cb"].dropna(),
            "gas_spot": stored["gas_spot"].dropna(),
            "gas_retail": stored["gas_retail"].dropna(),
            "sp500": stored["sp500"].dropna(),
            "dnsi": stored["dnsi"].dropna(),
        }

    data = {
        "michigan": pull_michigan(),
        "cb": pull_cb_confidence(),
        "gas_spot": pull_eia_series(EIA_GAS_SPOT),
        "gas_retail": pull_eia_series(EIA_GAS_RETAIL),
        "sp500": pull_sp500(),
        "dnsi": pull_dnsi(),
    }
    if cache is not None:
        merged = data["michigan"].copy()
        for name in ("cb", "gas_spot", "gas_retail", "sp500", "dnsi"):
            merged = merged.join(data[name].rename(name), how="outer")
        merged.to_csv(cache)
    return data
