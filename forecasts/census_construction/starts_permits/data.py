"""Data pulls for the housing starts + permits forecasts (research harness;
the production sources depend on the bake-off winners -- collectors are built
AFTER the bake-off in this initiative).

Targets: total housing starts and total building permits (SAAR, thousands)
for the next month M of the joint Census/HUD New Residential Construction
release (~the 17th of M+1, 08:30 ET; permits and starts publish together, so
at the origin both are known only through M-1).

Candidate inputs and their point-in-time status at the origin:

  * Census history workbooks (starts_cust.xlsx / permits_cust.xlsx,
    "Seasonally Adjusted" sheet): total + 1-unit + 2-4 + 5+ SAAR from 1959.
    ~Half of single-family homes start the month the permit issues and >90%
    within two months -- the SF permits->starts bridge is the structural
    edge; 5+ (multifamily) is lumpy with a long pipeline.
  * NAHB/Wells Fargo HMI (t2 history .xls, link discovered from the NAHB
    page): month M is released ~the 16th of M, well before the origin --
    lag 0 legal. Fed-validated predictor of starts (Goodman 1994).
  * Freddie Mac PMMS 30-yr mortgage rate (weekly Thursdays, 1971+): month M
    fully covered at the origin.
  * NOAA contiguous-US average temperature (Climate at a Glance CSV,
    keyless): month M posts ~the 8th of M+1 -- lag 0 legal. Weather is the
    canonical noise source for winter starts.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

CENSUS_XLSX = {
    "starts": "https://www.census.gov/construction/nrc/xls/starts_cust.xlsx",
    "permits": "https://www.census.gov/construction/nrc/xls/permits_cust.xlsx",
}
NAHB_HMI_PAGE = (
    "https://www.nahb.org/news-and-economics/housing-economics/indices/housing-market-index"
)
PMMS_URL = "https://www.freddiemac.com/pmms/docs/historicalweeklydata.xlsx"
# The CAG endpoint 404s on far-future end years; build the bound at call time.
NOAA_TAVG_URL_FMT = (
    "https://www.ncei.noaa.gov/cag/national/time-series/110-tavg-all-1-1959-{end_year}.csv"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

_MONTH_COLS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _get(url: str) -> bytes:
    from collectors.common.http import client, with_retries

    with client(timeout=120.0) as http:

        def call() -> bytes:
            response = http.get(url, headers={"User-Agent": BROWSER_UA})
            response.raise_for_status()
            return response.content

        return with_retries(call)


def pull_census(kind: str) -> pd.DataFrame:
    """Monthly SAAR frame (thousands) with ``total``, ``sf`` (1 unit),
    ``mf24`` (2-4 units), ``mf5`` (5+) from the Census history workbook."""
    raw = pd.read_excel(
        io.BytesIO(_get(CENSUS_XLSX[kind])), sheet_name="Seasonally Adjusted", header=None
    )
    frame = raw.iloc[6:, :5].copy()
    frame.columns = ["month", "total", "sf", "mf24", "mf5"]
    frame["month"] = pd.to_datetime(frame["month"], errors="coerce")
    frame = frame[frame["month"].notna()].set_index("month")
    for col in ("total", "sf", "mf24", "mf5"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")  # (S)/(NA) -> NaN
    return frame.sort_index()


def pull_hmi() -> pd.Series:
    """NAHB national HMI (SA, monthly, 1985+). The t2 history workbook URL
    carries a per-release hash, so it is discovered from the page."""
    page = _get(NAHB_HMI_PAGE).decode("utf-8", errors="replace")
    match = re.search(r'href="(/-/media/[^"]*t2-national-hmi-history[^"]*?)"', page)
    if match is None:
        raise RuntimeError("t2 HMI history link not found on the NAHB page")
    url = "https://www.nahb.org" + match.group(1).replace("&amp;", "&")
    raw = pd.read_excel(io.BytesIO(_get(url)), sheet_name=0, header=None)
    rows = {}
    for _, row in raw.iterrows():
        year = pd.to_numeric(row.iloc[0], errors="coerce")
        if pd.isna(year) or not 1985 <= year <= 2100:
            continue
        for m in range(12):
            value = pd.to_numeric(row.iloc[1 + m], errors="coerce")
            if pd.notna(value):
                rows[pd.Timestamp(int(year), m + 1, 1)] = float(value)
    return pd.Series(rows, dtype=float).sort_index()


def pull_pmms() -> pd.Series:
    """Freddie Mac 30-yr fixed mortgage rate, weekly (1971+)."""
    raw = pd.read_excel(io.BytesIO(_get(PMMS_URL)), sheet_name=0, header=None)
    week = pd.to_datetime(raw.iloc[:, 0], errors="coerce")
    rate = pd.to_numeric(raw.iloc[:, 1], errors="coerce")
    keep = week.notna() & rate.notna()
    return pd.Series(rate[keep].to_numpy(), index=week[keep]).sort_index()


def pull_tavg() -> pd.Series:
    """NOAA contiguous-US monthly average temperature (deg F)."""
    from datetime import date

    text = _get(NOAA_TAVG_URL_FMT.format(end_year=date.today().year)).decode()
    frame = pd.read_csv(io.StringIO(text), comment="#")
    months = pd.to_datetime(frame["Date"].astype(str), format="%Y%m")
    return pd.Series(frame["Value"].to_numpy(dtype=float), index=months).sort_index()


PROJECT = "us-econ-51920"


def pull_census_bq(series: str, client=None) -> pd.DataFrame:
    """Monthly SAAR frame (total/sf/mf24/mf5) from BigQuery, latest vintage
    per (segment, month) -- the production path."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, segment, value
    FROM `{PROJECT}.census_construction.new_residential_construction`
    WHERE series = @series AND seasonally_adjusted
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY observation_month, segment ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("series", "STRING", series)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    wide = frame.pivot(index="observation_month", columns="segment", values="value")
    wide = wide.rename(
        columns={"single_family": "sf", "units_2_4": "mf24", "units_5_plus": "mf5"}
    ).reindex(columns=["total", "sf", "mf24", "mf5"])
    wide.index.name = "month"
    return wide.sort_index()


def pull_hmi_bq(client=None) -> pd.Series:
    """National HMI from BigQuery, latest vintage per month."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, value
    FROM `{PROJECT}.nahb_hmi.housing_market_index`
    WHERE measure = 'hmi'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY observation_month ORDER BY ingested_at DESC) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_month"])
    )


def pull_tavg_bq(client=None) -> pd.Series:
    """Contiguous-US monthly average temperature from BigQuery, latest vintage."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, value
    FROM `{PROJECT}.noaa_climate.climate_at_a_glance`
    WHERE measure = 'tavg' AND region = 'contiguous_us'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY observation_month ORDER BY ingested_at DESC) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_month"])
    )


def pull_panel(cache: str | Path | None = None) -> dict:
    """All raw inputs for the harness, cached as one CSV."""
    if cache is not None and Path(cache).exists():
        stored = pd.read_csv(cache, index_col=0, parse_dates=True)
        return {
            "starts": stored[["st_total", "st_sf", "st_mf24", "st_mf5"]]
            .dropna(how="all")
            .rename(columns=lambda c: c[3:]),
            "permits": stored[["pm_total", "pm_sf", "pm_mf24", "pm_mf5"]]
            .dropna(how="all")
            .rename(columns=lambda c: c[3:]),
            "hmi": stored["hmi"].dropna(),
            "mortgage": stored["mortgage"].dropna(),
            "tavg": stored["tavg"].dropna(),
        }

    data = {
        "starts": pull_census("starts"),
        "permits": pull_census("permits"),
        "hmi": pull_hmi(),
        "mortgage": pull_pmms(),
        "tavg": pull_tavg(),
    }
    if cache is not None:
        merged = (
            data["starts"]
            .rename(columns=lambda c: f"st_{c}")
            .join(data["permits"].rename(columns=lambda c: f"pm_{c}"), how="outer")
        )
        for name in ("hmi", "mortgage", "tavg"):
            merged = merged.join(data[name].rename(name), how="outer")
        merged.to_csv(cache)
    return data
