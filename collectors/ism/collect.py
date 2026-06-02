import logging
import re
from datetime import date

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries
from collectors.ism.parser import parse_report

_log = logging.getLogger(__name__)

# ismworld.org gates the reports behind an SSO login; ISM distributes them
# publicly via PR Newswire. We discover the latest of each report from ISM's
# PR Newswire newsroom (listed newest-first) and parse the release.
NEWSROOM = "https://www.prnewswire.com/news/institute-for-supply-management/"
BASE = "https://www.prnewswire.com"
TABLE = "ism.report_on_business"

# (report, release-slug pattern, business-day-of-month it publishes on).
REPORTS = [
    (
        "manufacturing",
        re.compile(r"/news-releases/[a-z0-9-]+-ism-manufacturing-pmi-report-\d+\.html"),
        1,
    ),
    ("services", re.compile(r"/news-releases/[a-z0-9-]+-ism-services-pmi-report-\d+\.html"), 3),
]

# Long schema with a `report` dimension so Manufacturing and Services share one
# table. Append-only (no merge_keys): preserve every vintage, like the
# Conference Board collector. `release_month` is the report's reference/survey
# month (the report is named for month M but released early in M+1).
SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("report", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
    bigquery.SchemaField("release_month", "DATE", mode="REQUIRED"),
]


def collect(settings: Settings) -> LoadSpec:
    today = date.today()
    bday = _business_day_of_month(today)
    # Report for survey month M is released early in M+1, so the fresh report's
    # reference month is the immediately-prior calendar month.
    expected = _prior_month(date(today.year, today.month, 1))

    rows: list[dict] = []
    with client() as http:
        for report, pattern, release_bday in REPORTS:
            if bday != release_bday:
                continue
            rows += _fetch_parse(http, report, pattern, expected)

    if not rows:
        _log.info(
            "no ISM report due/fresh today; skipping load",
            extra={"extras": {"date": today.isoformat(), "business_day": bday}},
        )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _fetch_parse(
    http: httpx.Client, report: str, pattern: re.Pattern, expected: date
) -> list[dict]:
    url = _discover_latest(http, report, pattern)
    html = _get(http, url)
    result = parse_report(html, report)
    if result.reference_month != expected:
        # The expected month isn't published yet (or release slipped a day);
        # skip rather than re-ingest a stale report. Next month restates it.
        _log.warning(
            "latest ISM report is not the expected month; skipping",
            extra={
                "extras": {
                    "report": report,
                    "found": result.reference_month.isoformat(),
                    "expected": expected.isoformat(),
                    "url": url,
                }
            },
        )
        return []
    for w in result.warnings:
        _log.warning("ISM consistency check", extra={"extras": {"detail": w}})
    _log.info(
        "ISM report parsed",
        extra={
            "extras": {
                "report": report,
                "reference_month": result.reference_month.isoformat(),
                "row_count": len(result.rows),
                "url": url,
            }
        },
    )
    return result.rows


def _discover_latest(http: httpx.Client, report: str, pattern: re.Pattern) -> str:
    html = _get(http, NEWSROOM)
    match = pattern.search(html)  # first in document order = latest
    if match is None:
        raise RuntimeError(f"no {report} ISM report link found on {NEWSROOM}")
    return BASE + match.group(0)


def _get(http: httpx.Client, url: str) -> str:
    def call() -> str:
        response = http.get(url, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _prior_month(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def _business_day_of_month(d: date) -> int:
    """Count of weekdays (Mon-Fri) from the 1st through ``d`` inclusive.

    ISM publishes Manufacturing on the 1st business day and Services on the 3rd.
    Holiday-naive (like the other release-day gates here); a holiday shift is
    recovered the next month via the prior-month restatement in the table.
    """
    return sum(1 for day in range(1, d.day + 1) if date(d.year, d.month, day).weekday() < 5)
