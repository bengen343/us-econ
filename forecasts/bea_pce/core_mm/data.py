"""Data pulls for the core-PCE m/m forecast (research harness + production).

Target: the m/m % change of the PCE price index excluding food and energy
(BEA NIPA table T20804 monthly, series ``DPCCRG``), released with Personal
Income and Outlays ~the 25th-31st of M+1.

Core PCE is largely CONSTRUCTED from already-published source data, and at
the origin everything for month M is weeks old -- the friendliest
point-in-time case in the repo:

  * Core CPI (SA, ``CUSR0000SA0L1E``): the dominant input, released
    ~the 10th-13th of M+1. Most PCE components are deflated by CPI items.
  * The famous PPI add-ons, released ~the 11th-16th of M+1: portfolio
    management (tracks equity markets; also proxied by the S&P 500
    directly), physician offices + hospitals (PCE healthcare uses PPI, not
    CPI, for much of medical), and scheduled passenger air transportation.
    (The public frontier -- Employ America's Core-Cast -- replicates BEA's
    full accounting from these inputs to ~2bp average error; we regress on
    the headline pieces instead of rebuilding the accounting.)
  * S&P 500 (month-M average, our BigQuery): the portfolio-management fee
    proxy.

The harness pulls BLS series from the API (full history) and BEA from the
API (key in Secret Manager); production reads BigQuery + the BEA API.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from collectors.common.bls import fetch_series, parse_value

PROJECT = "us-econ-51920"

BEA_API_URL = "https://apps.bea.gov/api/data"
BEA_TABLE = "T20804"
PCE_CORE = "DPCCRG"  # PCE excluding food and energy, price index
PCE_HEADLINE = "DPCERG"

CPI_CORE_SA = "CUSR0000SA0L1E"
# Portfolio management PPI (PCU523940523940 under the 2022 NAICS) only starts
# 2022 -- too short to backtest (the pre-2022 series is gone from the API).
# The S&P 500 proxies it over the full history; the actual series is pulled
# for reference and is the documented upgrade once history accrues.
PPI_PORTFOLIO = "PCU523940523940"
PPI_PHYSICIANS = "PCU621111621111"  # offices of physicians
PPI_HOSPITALS = "PCU622110622110"  # general medical & surgical hospitals
PPI_AIRFARES = "PCU481111481111"  # scheduled passenger air transportation

_BLS_START_YEAR = 1990


def pull_bea_pce(api_key: str | None = None, series: list[str] | None = None) -> pd.DataFrame:
    """Monthly PCE price indexes from the BEA API (NIPA T20804)."""
    import httpx

    from collectors.common.secrets import get_secret

    api_key = api_key or get_secret(PROJECT, "bea-api-key")
    series = series or [PCE_CORE, PCE_HEADLINE]
    frames: dict[str, dict[pd.Timestamp, float]] = {s: {} for s in series}
    with httpx.Client(timeout=120) as http:
        current_year = date.today().year
        for start in range(1959, current_year + 1, 20):
            years = ",".join(str(y) for y in range(start, min(start + 20, current_year + 1)))
            response = http.get(
                BEA_API_URL,
                params={
                    "UserID": api_key,
                    "method": "GetData",
                    "DataSetName": "NIPA",
                    "TableName": BEA_TABLE,
                    "Frequency": "M",
                    "Year": years,
                    "ResultFormat": "JSON",
                },
            )
            response.raise_for_status()
            body = response.json()
            results = (body.get("BEAAPI") or {}).get("Results") or {}
            error = results.get("Error") or (body.get("BEAAPI") or {}).get("Error")
            if error:
                raise RuntimeError(f"BEA API error: {error}")
            for point in results.get("Data", []):
                code = point.get("SeriesCode")
                if code not in frames or "M" not in point.get("TimePeriod", ""):
                    continue
                year, month = point["TimePeriod"].split("M")
                stamp = pd.Timestamp(int(year), int(month), 1)
                frames[code][stamp] = float(point["DataValue"].replace(",", ""))
    return pd.DataFrame({s: pd.Series(v, dtype=float).sort_index() for s, v in frames.items()})


def _points_to_series(points: list[dict]) -> pd.Series:
    rows = {}
    for p in points:
        if not p["period"].startswith("M") or p["period"] == "M13":
            continue
        rows[pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1)] = parse_value(p.get("value"))
    return pd.Series(rows, dtype=float).sort_index()


def pull_bls(api_key: str | None = None) -> pd.DataFrame:
    """Core CPI + the PPI add-on series from the BLS API."""
    ids = [CPI_CORE_SA, PPI_PORTFOLIO, PPI_PHYSICIANS, PPI_HOSPITALS, PPI_AIRFARES]
    by_series = fetch_series(ids, _BLS_START_YEAR, date.today().year, api_key=api_key)
    return pd.DataFrame(
        {
            "core_cpi": _points_to_series(by_series[CPI_CORE_SA]),
            "ppi_portfolio": _points_to_series(by_series[PPI_PORTFOLIO]),
            "ppi_physicians": _points_to_series(by_series[PPI_PHYSICIANS]),
            "ppi_hospitals": _points_to_series(by_series[PPI_HOSPITALS]),
            "ppi_airfares": _points_to_series(by_series[PPI_AIRFARES]),
        }
    )


def pull_sp500_monthly(client=None) -> pd.Series:
    """Month-mean S&P 500 close from BigQuery (collectors/market_indexes)."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT DATE_TRUNC(observation_date, MONTH) AS month, AVG(close) AS value
    FROM `{PROJECT}.market_indexes.daily`
    WHERE ticker = '^GSPC'
    GROUP BY month ORDER BY month
    """
    frame = client.query(sql).to_dataframe()
    return pd.Series(frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["month"]))


def pull_core_pce_bq(client=None) -> pd.Series:
    """Core PCE price index from BigQuery (collectors/bea_pce), latest
    vintage per month -- the production path."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, value
    FROM `{PROJECT}.bea_pce.price_indexes`
    WHERE series_code = @code
    QUALIFY ROW_NUMBER() OVER (PARTITION BY observation_month ORDER BY ingested_at DESC) = 1
    ORDER BY observation_month
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", PCE_CORE)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_month"])
    )


def pull_bls_bq(series_id: str, table: str, client=None) -> pd.Series:
    """A BLS index series from BigQuery (bls_cpi.cpi_series /
    bls_ppi.ppi_series), latest vintage per month."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_date, value
    FROM `{PROJECT}.{table}`
    WHERE series_id = @sid
    QUALIFY ROW_NUMBER() OVER (PARTITION BY observation_date ORDER BY ingested_at DESC) = 1
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", series_id)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )


def maybe_bls_key() -> str | None:
    try:
        from collectors.common.secrets import get_secret

        return get_secret(PROJECT, "bls-api-key")
    except Exception:
        return None


def pull_panel(cache: str | Path | None = None) -> pd.DataFrame:
    """Monthly panel for the harness."""
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    bea = pull_bea_pce()
    panel = bea.rename(columns={PCE_CORE: "core_pce", PCE_HEADLINE: "headline_pce"})
    panel = panel.join(pull_bls(api_key=maybe_bls_key()), how="outer")
    panel = panel.join(pull_sp500_monthly().rename("sp500"), how="outer")
    panel.index = pd.to_datetime(panel.index)  # joins can degrade to object Index
    panel = panel.sort_index()
    panel.index.name = "month"

    if cache is not None:
        panel.to_csv(cache)
    return panel
