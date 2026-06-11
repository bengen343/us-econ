"""Data pulls for the retail-sales headline forecast (research harness; the
production sources depend on the bake-off winners -- collectors are built
AFTER the bake-off, per the housing-starts process).

Target: the m/m % change of total retail & food services sales (SA, nominal
$M) -- the headline of the Census Advance Monthly Retail Trade Survey
(MARTS), released ~the 15th-17th of M+1 (08:30 ET, ~10 business days after
month end).

Retail sales are NOMINAL, and the volatile components have observable
month-M drivers, all published before the origin:

  * Light vehicle unit sales (SAAR), ~the 2nd business day of M+1 -- motor
    vehicle & parts dealers are ~20% of the headline. Harness source: BEA
    underlying-detail table 7.2.5S (autos SAAR + light trucks SAAR,
    1976+); the live month comes via FRED's TOTALSA mirror in production.
  * Retail gasoline prices (EIA weekly, in our BigQuery) -- gasoline
    stations are ~8% of the headline and their nominal sales track the pump
    price; month M is fully elapsed at the origin.
  * CPI for month M (~the 10th-13th of M+1, before MARTS in the modern
    calendar -- ordering was occasionally reversed pre-2010s, a PIT caveat
    flagged in the harness) -- the broad deflator for everything else.
  * Michigan sentiment (final for M, ~4th Friday of M; in our BigQuery).

MARTS history: the per-NAICS advance time-series txt files on census.gov
(e.g. adv44X72.txt = retail & food services total; SA estimates block
followed by a SEASONAL FACTORS block). Latest vintage -- advance prints are
revised in place (caveat in the harness).
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

PROJECT = "us-econ-51920"

MARTS_TXT_FMT = "https://www.census.gov/retail/marts/www/adv{code}.txt"
MARTS_HEADLINE = "44X72"  # retail & food services, total
BEA_SECTION7_URL = "https://apps.bea.gov/national/Release/XLS/Underlying/Section7All_xls.xlsx"
EIA_GAS_RETAIL = "EMM_EPM0_PTE_NUS_DPG"  # all grades retail, weekly Mondays
CPI_HEADLINE_SA = "CUSR0000SA0"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)


def _get(url: str) -> bytes:
    from collectors.common.http import client, with_retries

    with client(timeout=300.0) as http:

        def call() -> bytes:
            response = http.get(url, headers={"User-Agent": BROWSER_UA})
            response.raise_for_status()
            return response.content

        return with_retries(call)


def parse_marts_txt(text: str) -> pd.Series:
    """The SA estimates block of a MARTS advance time-series txt: a title
    line, a YEAR/JAN..DEC header, then year rows until the SEASONAL FACTORS
    block. Values in $M, '(NA)' blank."""
    rows: dict[pd.Timestamp, float] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("SEASONAL FACTORS"):
            break
        parts = stripped.split()
        if not parts or not parts[0].isdigit() or not 1990 <= int(parts[0]) <= 2100:
            continue
        year = int(parts[0])
        for m, raw in enumerate(parts[1:13]):
            try:
                rows[pd.Timestamp(year, m + 1, 1)] = float(raw)
            except ValueError:
                continue  # (NA) etc.
    return pd.Series(rows, dtype=float).sort_index()


def pull_marts(code: str = MARTS_HEADLINE) -> pd.Series:
    """A MARTS advance series (SA, $M) from the census.gov txt files."""
    return parse_marts_txt(_get(MARTS_TXT_FMT.format(code=code)).decode("latin-1"))


def pull_vehicles(cache_xlsx: str | Path | None = None) -> pd.Series:
    """Light vehicle unit sales (autos + light trucks, SAAR millions) from
    BEA underlying-detail table 7.2.5S (sheet U70205S-M)."""
    if cache_xlsx is not None and Path(cache_xlsx).exists():
        content = Path(cache_xlsx).read_bytes()
    else:
        content = _get(BEA_SECTION7_URL)
        if cache_xlsx is not None:
            Path(cache_xlsx).write_bytes(content)
    frame = pd.read_excel(io.BytesIO(content), sheet_name="U70205S-M", header=None)
    months = [pd.Timestamp(int(m[:4]), int(m[5:]), 1) for m in frame.iloc[7, 3:]]

    def row(code: str):
        i = frame[frame.iloc[:, 2] == code].index[0]
        return pd.to_numeric(frame.iloc[i, 3:], errors="coerce").to_numpy()

    # Autos SAAR (millions) + light trucks <=14k lbs domestic + imported SAAR.
    total = row("SAART") + row("TEMF") + row("TEMG")
    return pd.Series(total, index=months, dtype=float).dropna().sort_index()


def pull_eia_gas_monthly(client=None) -> pd.Series:
    """Monthly mean retail gasoline price ($/gal) from BigQuery."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT DATE_TRUNC(observation_date, MONTH) AS month, AVG(value) AS value
    FROM `{PROJECT}.eia_petroleum.prices`
    WHERE series_id = @sid
    GROUP BY month ORDER BY month
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", EIA_GAS_RETAIL)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["month"]))


def pull_cpi_headline(client=None) -> pd.Series:
    """Headline CPI (SA index), latest vintage per month, from BigQuery."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_date, value
    FROM `{PROJECT}.bls_cpi.cpi_series`
    WHERE series_id = @sid
    QUALIFY ROW_NUMBER() OVER (PARTITION BY observation_date ORDER BY ingested_at DESC) = 1
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", CPI_HEADLINE_SA)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )


def pull_michigan_final(client=None) -> pd.Series:
    """Michigan ICS final, from BigQuery."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, value
    FROM `{PROJECT}.michigan_sentiment.surveys_of_consumers`
    WHERE measure = 'sentiment' AND release_type = 'final'
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
        return {name: stored[name].dropna() for name in stored.columns}

    data = {
        "retail": pull_marts(),
        "vehicles": pull_vehicles(cache_xlsx="_bea_section7.xlsx"),
        "gas": pull_eia_gas_monthly(),
        "cpi": pull_cpi_headline(),
        "sentiment": pull_michigan_final(),
    }
    if cache is not None:
        merged = pd.DataFrame(data)
        merged.to_csv(cache)
    return data
