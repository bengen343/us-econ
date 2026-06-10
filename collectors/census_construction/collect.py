"""Census/HUD New Residential Construction collector.

Lands the four series of the joint monthly release -- housing starts,
building permits, completions, and units under construction -- into
``census_construction.new_residential_construction``, parsed from the
official full-history workbooks on census.gov (keyless; the EITS API now
requires a registered key, and the workbooks are restated in full at every
release):

    https://www.census.gov/construction/nrc/xls/{starts,permits,comps,under}_cust.xlsx

Each workbook carries "Seasonally Adjusted" (annual-rate) and "Not
Seasonally Adjusted" sheets with United States total / 1 unit / 2-4 units /
5+ units columns; suppressed cells ("(S)", "(NA)") load as NULL.

Append-only and vintage-stamped (like the BLS collectors): starts, permits
and completions are revised for two months after first print (average
revision <= ~2.9%), and the release calendar drifts (~the 16th-19th of
M+1), so the job runs daily through that window and re-appends the current
workbook contents; consumers dedupe to the latest vintage per
(series, segment, adjustment, month) via ``ingested_at``, and first prints
accrue for revision studies.
"""

import io
import logging

import pandas as pd
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "census_construction.new_residential_construction"
BASE_URL = "https://www.census.gov/construction/nrc/xls/{slug}_cust.xlsx"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

SERIES = {
    "starts": "starts",
    "permits": "permits",
    "completions": "comps",
    "under_construction": "under",
}
SEGMENTS = ["total", "single_family", "units_2_4", "units_5_plus"]
SHEETS = {
    # sheet name -> (seasonally_adjusted, units). SA levels are annual rates
    # for the flow series; under-construction is a month-end stock.
    "Seasonally Adjusted": (True, "thousands of units (SAAR)"),
    "Not Seasonally Adjusted": (False, "thousands of units"),
}

SCHEMA: list[bigquery.SchemaField] = [
    # starts | permits | completions | under_construction
    bigquery.SchemaField("series", "STRING", mode="REQUIRED"),
    # total | single_family | units_2_4 | units_5_plus (United States)
    bigquery.SchemaField("segment", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("seasonally_adjusted", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]


def collect(settings: Settings) -> LoadSpec:
    rows: list[dict] = []
    with client(timeout=120.0) as http:
        for series, slug in SERIES.items():

            def call(slug: str = slug) -> bytes:
                response = http.get(BASE_URL.format(slug=slug), headers={"User-Agent": BROWSER_UA})
                response.raise_for_status()
                return response.content

            workbook = with_retries(call)
            series_rows = _parse_workbook(workbook, series)
            rows.extend(series_rows)
            _log.info(
                "NRC workbook parsed",
                extra={"extras": {"series": series, "rows": len(series_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _parse_workbook(content: bytes, series: str) -> list[dict]:
    rows: list[dict] = []
    for sheet, (seasonally_adjusted, units) in SHEETS.items():
        raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet, header=None)
        # Data rows start after the two header rows; col 0 = month, cols 1-4 =
        # United States total / 1 unit / 2-4 / 5+ (regional columns follow).
        frame = raw.iloc[6:, :5].copy()
        frame.columns = ["month", *SEGMENTS]
        frame["month"] = pd.to_datetime(frame["month"], errors="coerce")
        frame = frame[frame["month"].notna()]
        for segment in SEGMENTS:
            values = pd.to_numeric(frame[segment], errors="coerce")  # (S)/(NA) -> NULL
            for month, value in zip(frame["month"], values, strict=True):
                rows.append(
                    {
                        "series": series,
                        "segment": segment,
                        "seasonally_adjusted": seasonally_adjusted,
                        "observation_month": month.date().isoformat(),
                        "value": None if pd.isna(value) else float(value),
                        "units": units,
                    }
                )
    return rows
