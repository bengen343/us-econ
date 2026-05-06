import logging
from datetime import date

from google.cloud import bigquery

from collectors.claims import doleta_xml, press_pdf
from collectors.common import LoadSpec, Settings
from collectors.common.http import client

_log = logging.getLogger(__name__)

TABLE = "claims.weekly_claims"
LOOKBACK_YEARS = 20

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("level", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("area", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("seasonal_adjustment", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("units", "STRING"),
    bigquery.SchemaField("week_ending", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("vintage_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
]


def collect(settings: Settings) -> LoadSpec:
    end_year = date.today().year
    start_year = end_year - LOOKBACK_YEARS

    rows: list[dict] = []
    with client() as http:
        doleta = doleta_xml.fetch_all(http, start_year=start_year, end_year=end_year)
        rows.extend(doleta.rows)
        _log.info(
            "doleta XML rows fetched",
            extra={
                "extras": {
                    "row_count": len(doleta.rows),
                    "rundate": doleta.rundate.isoformat(),
                }
            },
        )

        press = press_pdf.fetch(http)
        rows.extend(press.rows)
        _log.info(
            "press PDF rows fetched",
            extra={
                "extras": {
                    "row_count": len(press.rows),
                    "vintage_date": press.vintage_date.isoformat() if press.vintage_date else None,
                }
            },
        )

    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)
