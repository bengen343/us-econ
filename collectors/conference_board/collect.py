import calendar
import logging
from datetime import date

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries
from collectors.conference_board.parser import parse_release

_log = logging.getLogger(__name__)

# Stable landing page that always serves the latest release inline (verified).
URL = "https://www.conference-board.org/topics/consumer-confidence/"
TABLE = "conference_board.consumer_confidence"

# Long format so "capture every reliable series" is just more rows, never a
# schema change. Append-only (no merge_keys): we never overwrite, so every
# observation we ever saw is preserved (incl. the prior-month restatements that
# give a revision trail) — the landing page only shows the latest month, so we
# play it safe and keep everything. Downstream dedupes latest by ingested_at.
SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
    bigquery.SchemaField("release_month", "DATE", mode="REQUIRED"),
]


def collect(settings: Settings) -> LoadSpec:
    today = date.today()
    if not _is_last_tuesday(today):
        _log.info(
            "skipping non-release day (Consumer Confidence releases the last Tuesday)",
            extra={"extras": {"date": today.isoformat(), "weekday": today.strftime("%A")}},
        )
        return LoadSpec(table=TABLE, schema=SCHEMA, rows=[])

    with client() as http:
        html = _fetch(http, URL)

    result = parse_release(html)
    for w in result.warnings:
        _log.warning("consistency check", extra={"extras": {"detail": w}})
    _log.info(
        "Conference Board Consumer Confidence parsed",
        extra={
            "extras": {
                "release_month": result.release_month.isoformat(),
                "row_count": len(result.rows),
                "measures": sorted({r["measure"] for r in result.rows}),
                "url": URL,
            }
        },
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=result.rows)


def _fetch(http: httpx.Client, url: str) -> str:
    def call() -> str:
        response = http.get(url, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _is_last_tuesday(d: date) -> bool:
    """True only on the last Tuesday of the month (the release day).

    Cron can't express 'last Tuesday', so the scheduler fires every Tuesday and
    this gate keeps only the final one. A missed run self-heals: the next
    release restates this month's figures via the prior-month capture.
    """
    last_dom = calendar.monthrange(d.year, d.month)[1]
    return d.weekday() == 1 and d.day > last_dom - 7
