import logging
import re
from datetime import date

import httpx
from google.cloud import bigquery

from collectors.challenger_employment.parser import ParseResult, parse_report
from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

BLOG_CATEGORY_URL = "https://www.challengergray.com/blog/category/job-cuts-report/"

MONTHLY_TABLE = "challenger_employment.monthly"
CUT_REASONS_TABLE = "challenger_employment.cut_reasons"
QUARTERLY_TABLE = "challenger_employment.quarterly"

# First post link on the blog category page that targets a /blog/<slug>/ URL.
# The category-listing links (.../blog/category/...) and the page's own canonical
# self-links are excluded. The first match in document order is the latest post.
_POST_LINK_RE = re.compile(
    r'https://www\.challengergray\.com/blog/(?!category/)[a-z0-9-]+/'
)
# PDF asset URL inside a post's "Download the Full Report" button.
_PDF_LINK_RE = re.compile(
    r'https://www\.challengergray\.com/wp-content/uploads/\d{4}/\d{2}/'
    r'Challenger-Report-[^"\'\s]+\.pdf'
)

MONTHLY_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("breakdown", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("region", "STRING"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
]
MONTHLY_MERGE_KEYS = ["series", "breakdown", "category", "region", "observation_month"]

CUT_REASONS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("reason", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
]
CUT_REASONS_MERGE_KEYS = ["reason", "observation_month"]

QUARTERLY_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("quarter_start", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
]
QUARTERLY_MERGE_KEYS = ["quarter_start"]


def collect(settings: Settings) -> list[LoadSpec]:
    today = date.today()
    if not _is_first_thursday(today):
        _log.info(
            "skipping non-release weekday",
            extra={"extras": {"date": today.isoformat(), "weekday": today.strftime("%A")}},
        )
        return _empty_specs()

    with client() as http:
        post_url = _discover_latest_post(http)
        pdf_url = _discover_pdf_url(http, post_url)
        pdf_bytes = _download_pdf(http, pdf_url)

    result = parse_report(pdf_bytes)
    _log.info(
        "challenger PDF parsed",
        extra={
            "extras": {
                "report_month": result.report_month.isoformat(),
                "release_date": result.release_date.isoformat(),
                "pdf_url": pdf_url,
                "monthly_rows": len(result.monthly_rows),
                "cut_reasons_rows": len(result.cut_reasons_rows),
                "quarterly_rows": len(result.quarterly_rows),
            }
        },
    )
    return _build_specs(result)


def _build_specs(result: ParseResult) -> list[LoadSpec]:
    return [
        LoadSpec(
            table=MONTHLY_TABLE,
            schema=MONTHLY_SCHEMA,
            rows=result.monthly_rows,
            merge_keys=MONTHLY_MERGE_KEYS,
        ),
        LoadSpec(
            table=CUT_REASONS_TABLE,
            schema=CUT_REASONS_SCHEMA,
            rows=result.cut_reasons_rows,
            merge_keys=CUT_REASONS_MERGE_KEYS,
        ),
        LoadSpec(
            table=QUARTERLY_TABLE,
            schema=QUARTERLY_SCHEMA,
            rows=result.quarterly_rows,
            merge_keys=QUARTERLY_MERGE_KEYS,
        ),
    ]


def _empty_specs() -> list[LoadSpec]:
    return [
        LoadSpec(MONTHLY_TABLE, MONTHLY_SCHEMA, [], MONTHLY_MERGE_KEYS),
        LoadSpec(CUT_REASONS_TABLE, CUT_REASONS_SCHEMA, [], CUT_REASONS_MERGE_KEYS),
        LoadSpec(QUARTERLY_TABLE, QUARTERLY_SCHEMA, [], QUARTERLY_MERGE_KEYS),
    ]


def _discover_latest_post(http: httpx.Client) -> str:
    def call() -> str:
        response = http.get(BLOG_CATEGORY_URL, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    html = with_retries(call)
    match = _POST_LINK_RE.search(html)
    if match is None:
        raise RuntimeError(f"no blog post link found on {BLOG_CATEGORY_URL}")
    return match.group(0)


def _discover_pdf_url(http: httpx.Client, post_url: str) -> str:
    def call() -> str:
        response = http.get(post_url, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    html = with_retries(call)
    match = _PDF_LINK_RE.search(html)
    if match is None:
        raise RuntimeError(f"no Challenger PDF link found on post page {post_url}")
    return match.group(0)


def _download_pdf(http: httpx.Client, pdf_url: str) -> bytes:
    def call() -> bytes:
        response = http.get(pdf_url, headers={"Accept": "application/pdf"})
        response.raise_for_status()
        return response.content

    return with_retries(call)


def _is_first_thursday(d: date) -> bool:
    return d.weekday() == 3 and d.day <= 7
