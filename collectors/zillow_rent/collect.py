"""Zillow Observed Rent Index (ZORI) collector.

Lands the national ZORI -- a smoothed, repeat-rent measure of typical observed
market-rate rent -- the validated market-rent input for nowcasting CPI shelter
(Ball & Koh, NBER w34113, take the ZORI path as exogenous to derive implied CPI
shelter inflation). Market/new-tenant rents lead CPI shelter by roughly a year
because most leases are annual, renewal pass-through is partial, and CPI's rent
measure compares against rent six months earlier.

Source is Zillow Research's public CSV (the "all homes plus multifamily,
smoothed" series), in both seasonally adjusted and not-seasonally-adjusted
flavors, served from the zillowstatic CDN. The file is wide (one column per
month); we keep only the U.S. national ("country") row. ZORI is revised as
Zillow re-smooths and rebenchmarks, so rows are append-only and vintage-stamped
(matching the BLS collectors) -- downstream takes the latest vintage per month,
but the revision history is preserved for point-in-time backtesting.
"""

import csv
import io
import logging
from datetime import date

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "zillow_rent.zori"
CDN = "https://files.zillowstatic.com/research/public_csvs/zori"
# "All homes plus multifamily, smoothed" national+metro series, SA and NSA.
SOURCES: list[tuple[str, bool]] = [
    (f"{CDN}/Metro_zori_uc_sfrcondomfr_sm_sa_month.csv", True),
    (f"{CDN}/Metro_zori_uc_sfrcondomfr_sm_month.csv", False),
]
# The national aggregate row in Zillow's metro-level file.
US_REGION_ID = "102001"
_META_COLS = 5  # RegionID, SizeRank, RegionName, RegionType, StateName

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("region_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("region_name", "STRING"),
    bigquery.SchemaField("region_type", "STRING"),
    bigquery.SchemaField("seasonally_adjusted", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),  # first of month
    bigquery.SchemaField("value", "FLOAT64"),  # typical observed rent, USD/month
]


def collect(settings: Settings) -> LoadSpec:
    rows: list[dict] = []
    with client() as http:
        for url, seasonally_adjusted in SOURCES:
            text = _fetch(http, url)
            parsed = _national_rows(text, seasonally_adjusted)
            rows.extend(parsed)
            _log.info(
                "ZORI file parsed",
                extra={"extras": {"url": url, "sa": seasonally_adjusted, "rows": len(parsed)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _fetch(http: httpx.Client, url: str) -> str:
    def call() -> str:
        response = http.get(url, headers={"Accept": "text/csv"})
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _national_rows(text: str, seasonally_adjusted: bool) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    date_cols = header[_META_COLS:]

    national: list[str] | None = None
    for record in reader:
        if record and record[0] == US_REGION_ID:
            national = record
            break
    if national is None:
        raise RuntimeError(f"ZORI national row (RegionID {US_REGION_ID}) not found")

    region_id, _size_rank, region_name, region_type = national[:4]
    rows: list[dict] = []
    for col, raw in zip(date_cols, national[_META_COLS:], strict=False):
        value = _parse_float(raw)
        if value is None:
            continue
        rows.append(
            {
                "region_id": region_id,
                "region_name": region_name,
                "region_type": region_type,
                "seasonally_adjusted": seasonally_adjusted,
                "observation_date": _month_start(col).isoformat(),
                "value": value,
            }
        )
    return rows


def _month_start(col: str) -> date:
    """Zillow column headers are month-end dates (YYYY-MM-DD); normalise to the
    first of that month to match the BLS / CPI observation_date convention."""
    parts = col.split("-")
    return date(int(parts[0]), int(parts[1]), 1)


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None
