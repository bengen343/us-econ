"""NAHB/Wells Fargo Housing Market Index collector.

Lands the national HMI (1985+) and its three components -- single-family
sales: present, single-family sales: next six months, traffic of prospective
buyers -- into ``nahb_hmi.housing_market_index``, parsed from the public
history workbooks on nahb.org (legacy .xls -- hence the xlrd dependency):

  * Table 2 "National HMI - History": year rows x Jan..Dec columns.
  * Table 3 "National HMI Components - History": three stacked blocks, each
    a month-row x year-column grid under its title row.

The workbook URLs carry a per-release hash
(/-/media/.../<yyyy-mm>/t2-national-hmi-history-<yyyymm>.xls?rev=...), so
each run discovers the current links from the HMI page.

The HMI is the month-M survey input of the housing-starts forecast
(forecasts/census_construction/starts_permits): it is released ~the 16th of
month M itself, a month before the M starts print. Append-only and
vintage-stamped -- the history is seasonally adjusted and occasionally
restated, so each post-release run re-appends the full workbook contents and
consumers dedupe to the latest vintage via ``ingested_at``.
"""

import io
import logging
import re

import pandas as pd
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "nahb_hmi.housing_market_index"
HMI_PAGE = "https://www.nahb.org/news-and-economics/housing-economics/indices/housing-market-index"
BASE = "https://www.nahb.org"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
UNITS = "diffusion index (SA)"

T2_RE = re.compile(r'href="(/-/media/[^"]*t2-national-hmi-history[^"]*?)"')
T3_RE = re.compile(r'href="(/-/media/[^"]*t3-national-hmi-components-history[^"]*?)"')

# Table 3 block title (column 0) -> measure slug.
COMPONENTS = {
    "single-family:  present": "sf_sales_present",
    "single-family: next six months": "sf_sales_next_6mo",
    "traffic of prospective buyers": "traffic_prospective_buyers",
}
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

SCHEMA: list[bigquery.SchemaField] = [
    # hmi | sf_sales_present | sf_sales_next_6mo | traffic_prospective_buyers
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]


def collect(settings: Settings) -> LoadSpec:
    with client(timeout=120.0) as http:
        page = _get(http, HMI_PAGE).decode("utf-8", errors="replace")
        rows = [
            *_parse_t2(_get(http, _discover(page, T2_RE, "t2"))),
            *_parse_t3(_get(http, _discover(page, T3_RE, "t3"))),
        ]
    by_measure = pd.Series([r["measure"] for r in rows]).value_counts().to_dict()
    _log.info("HMI workbooks parsed", extra={"extras": {"rows": len(rows), **by_measure}})
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _discover(page: str, pattern: re.Pattern, name: str) -> str:
    match = pattern.search(page)
    if match is None:
        raise RuntimeError(f"{name} HMI history link not found on {HMI_PAGE}")
    return BASE + match.group(1).replace("&amp;", "&")


def _get(http, url: str) -> bytes:
    def call() -> bytes:
        response = http.get(url, headers={"User-Agent": BROWSER_UA})
        response.raise_for_status()
        return response.content

    return with_retries(call)


def _parse_t2(content: bytes) -> list[dict]:
    """Table 2: year rows x Jan..Dec columns -> the headline HMI."""
    raw = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None)
    rows: list[dict] = []
    for _, row in raw.iterrows():
        year = pd.to_numeric(row.iloc[0], errors="coerce")
        if pd.isna(year) or not 1985 <= year <= 2100:
            continue
        for m in range(12):
            value = pd.to_numeric(row.iloc[1 + m], errors="coerce")
            if pd.notna(value):
                rows.append(_row("hmi", int(year), m + 1, float(value)))
    return rows


def _parse_t3(content: bytes) -> list[dict]:
    """Table 3: three stacked blocks of month rows x year columns."""
    raw = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None)
    rows: list[dict] = []
    measure: str | None = None
    years: list[float] = []
    for _, row in raw.iterrows():
        label = str(row.iloc[0]).strip().lower() if pd.notna(row.iloc[0]) else ""
        if label in COMPONENTS:
            measure = COMPONENTS[label]
            years = []
            continue
        if measure and not years and pd.notna(pd.to_numeric(row.iloc[1], errors="coerce")):
            years = [pd.to_numeric(v, errors="coerce") for v in row.iloc[1:]]
            continue
        month = _MONTHS.get(label.rstrip(".")[:3])
        if measure and years and month:
            for j, year in enumerate(years):
                value = pd.to_numeric(row.iloc[1 + j], errors="coerce")
                if pd.notna(year) and pd.notna(value):
                    rows.append(_row(measure, int(year), month, float(value)))
    return rows


def _row(measure: str, year: int, month: int, value: float) -> dict:
    return {
        "measure": measure,
        "observation_month": f"{year:04d}-{month:02d}-01",
        "value": value,
        "units": UNITS,
    }
