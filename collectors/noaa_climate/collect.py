"""NOAA Climate at a Glance collector.

Lands the contiguous-US monthly average temperature (1959+, degrees F) into
``noaa_climate.climate_at_a_glance``, from NCEI's keyless CSV endpoint:

    https://www.ncei.noaa.gov/cag/national/time-series/110-tavg-all-1-1959-<year>.csv

(the endpoint 404s on far-future end years, so the bound is built at run
time). Month M posts ~the 8th of M+1.

Temperature is the weather input of the housing-starts forecast
(forecasts/census_construction/starts_permits): the deviation of month-M
temperature from its calendar-month norm explains part of the winter noise
in starts, and month M is fully published before the mid-(M+1) release.

The schema carries ``measure``/``region`` dimensions so further CAG series
(precipitation, regional cuts) can land without a schema change. Append-only
and vintage-stamped -- NOAA's homogenization occasionally restates history,
so each run re-appends the full series and consumers dedupe to the latest
vintage via ``ingested_at``.
"""

import io
import logging
from datetime import date

import pandas as pd
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "noaa_climate.climate_at_a_glance"
URL_FMT = (
    "https://www.ncei.noaa.gov/cag/national/time-series/110-{measure}-all-1-1959-{end_year}.csv"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# measure slug -> units. Region 110 = contiguous United States.
MEASURES = {
    "tavg": "degrees Fahrenheit",
}
REGION = "contiguous_us"

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),  # tavg
    bigquery.SchemaField("region", "STRING", mode="REQUIRED"),  # contiguous_us
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]


def collect(settings: Settings) -> LoadSpec:
    rows: list[dict] = []
    with client(timeout=120.0) as http:
        for measure, units in MEASURES.items():
            url = URL_FMT.format(measure=measure, end_year=date.today().year)

            def call(url: str = url) -> str:
                response = http.get(url, headers={"User-Agent": BROWSER_UA})
                response.raise_for_status()
                return response.text

            text = with_retries(call)
            measure_rows = _parse_csv(text, measure, units)
            rows.extend(measure_rows)
            _log.info(
                "CAG series parsed",
                extra={"extras": {"measure": measure, "rows": len(measure_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _parse_csv(text: str, measure: str, units: str) -> list[dict]:
    frame = pd.read_csv(io.StringIO(text), comment="#")
    months = pd.to_datetime(frame["Date"].astype(str), format="%Y%m")
    values = pd.to_numeric(frame["Value"], errors="coerce")
    return [
        {
            "measure": measure,
            "region": REGION,
            "observation_month": month.date().isoformat(),
            "value": None if pd.isna(value) else float(value),
            "units": units,
        }
        for month, value in zip(months, values, strict=True)
    ]
