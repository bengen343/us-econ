"""Census/HUD New Residential Sales collector.

Lands the series of the monthly release -- new single-family houses sold,
the for-sale inventory, and the published months' supply -- into
``census_construction.new_residential_sales`` (the shared construction
dataset), parsed from the official full-history workbooks on census.gov
(keyless, same family as the NRC workbooks):

    https://www.census.gov/construction/nrs/xls/{sold,fsale}_cust.xlsx

Both use a single "Monthly" sheet but with different column layouts (mapped
explicitly below): sold carries NSA and SA blocks of US + four regions; the
for-sale workbook carries NSA US + regions, a published NSA months' supply,
then SA US and SA months' supply only.

``sold``/total/SA is the headline SAAR the new-home-sales forecast targets
(forecasts/census_construction/new_home_sales). Append-only and
vintage-stamped: the preliminary SA sales estimate revises ~5% on average
(the largest of the housing prints) and the release lands ~the 23rd-27th of
M+1 (10:00 ET), so the job runs daily through that window; consumers dedupe
to the latest vintage per (series, segment, adjustment, month) via
``ingested_at`` and first prints accrue for revision studies.
"""

import io
import logging

import pandas as pd
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "census_construction.new_residential_sales"
BASE_URL = "https://www.census.gov/construction/nrs/xls/{slug}_cust.xlsx"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

_UNITS_K = "thousands of units"
_UNITS_SAAR = "thousands of units (SAAR)"
_UNITS_STOCK = "thousands of units (end of period, SA)"
_UNITS_MONTHS = "months"

# slug -> list of (column index in the 'Monthly' sheet, series, segment,
# seasonally_adjusted, units). Layouts verified 2026-06.
WORKBOOKS: dict[str, list[tuple[int, str, str, bool, str]]] = {
    "sold": [
        (1, "sold", "total", False, _UNITS_K),
        (2, "sold", "northeast", False, _UNITS_K),
        (3, "sold", "midwest", False, _UNITS_K),
        (4, "sold", "south", False, _UNITS_K),
        (5, "sold", "west", False, _UNITS_K),
        (6, "sold", "total", True, _UNITS_SAAR),
        (7, "sold", "northeast", True, _UNITS_SAAR),
        (8, "sold", "midwest", True, _UNITS_SAAR),
        (9, "sold", "south", True, _UNITS_SAAR),
        (10, "sold", "west", True, _UNITS_SAAR),
    ],
    "fsale": [
        (1, "for_sale", "total", False, _UNITS_K),
        (2, "for_sale", "northeast", False, _UNITS_K),
        (3, "for_sale", "midwest", False, _UNITS_K),
        (4, "for_sale", "south", False, _UNITS_K),
        (5, "for_sale", "west", False, _UNITS_K),
        (6, "months_supply", "total", False, _UNITS_MONTHS),
        (7, "for_sale", "total", True, _UNITS_STOCK),
        (8, "months_supply", "total", True, _UNITS_MONTHS),
    ],
}


SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series", "STRING", mode="REQUIRED"),  # sold | for_sale | months_supply
    bigquery.SchemaField("segment", "STRING", mode="REQUIRED"),  # total | region
    bigquery.SchemaField("seasonally_adjusted", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]


def collect(settings: Settings) -> LoadSpec:
    rows: list[dict] = []
    with client(timeout=120.0) as http:
        for slug, columns in WORKBOOKS.items():

            def call(slug: str = slug) -> bytes:
                response = http.get(BASE_URL.format(slug=slug), headers={"User-Agent": BROWSER_UA})
                response.raise_for_status()
                return response.content

            workbook = with_retries(call)
            slug_rows = _parse_workbook(workbook, columns)
            rows.extend(slug_rows)
            _log.info(
                "NRS workbook parsed",
                extra={"extras": {"slug": slug, "rows": len(slug_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _parse_workbook(content: bytes, columns: list[tuple[int, str, str, bool, str]]) -> list[dict]:
    raw = pd.read_excel(io.BytesIO(content), sheet_name="Monthly", header=None)
    months = pd.to_datetime(raw.iloc[6:, 0], errors="coerce")
    keep = months.notna()

    rows: list[dict] = []
    for col, series, segment, adjusted, units in columns:
        values = pd.to_numeric(raw.iloc[6:, col], errors="coerce")  # (NA)/(S)/(Z) -> NULL
        for month, value in zip(months[keep], values[keep], strict=True):
            rows.append(
                {
                    "series": series,
                    "segment": segment,
                    "seasonally_adjusted": adjusted,
                    "observation_month": month.date().isoformat(),
                    "value": None if pd.isna(value) else float(value),
                    "units": units,
                }
            )
    return rows
