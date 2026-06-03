import logging
import re
from datetime import date

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries
from collectors.sp_global_pmi.parser import parse_release

_log = logging.getLogger(__name__)

# pmi.spglobal.com publishes each release as a PDF behind a stable hash URL,
# discovered from the public "PMI releases" listing (newest-first). The vendor's
# own marketing pages (spglobal.com/marketintelligence) gate automated fetches
# with 403s; this public PMI newsroom does not.
LISTING = "https://www.pmi.spglobal.com/Public/Release/PressReleases?language=en"
PRESS_BASE = "https://www.pmi.spglobal.com/Public/Home/PressRelease/"
# Lives in the shared `ism` dataset alongside ism.report_on_business — both are
# monthly business-survey diffusion indexes — under a distinct table name.
TABLE = "ism.sp_global_us_pmi"

# Exact listing titles -> the release_type we parse them as. The flash bundles
# Composite/Services/Manufacturing in one PDF; the final services release is its
# own PDF (final manufacturing is a separate release we don't target).
REPORTS = [
    ("S&P Global Flash US PMI", "flash"),
    ("S&P Global US Services PMI", "final"),
]

# Long schema with report/measure/release_type dimensions so flash and final are
# distinct vintages of the same survey month, and Services/Manufacturing/Composite
# share one table. Append-only (no merge_keys): preserve every vintage, like the
# ISM and Conference Board collectors. Downstream dedupes by latest ingested_at.
SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("report", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("release_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
    bigquery.SchemaField("release_date", "DATE", mode="REQUIRED"),
]

# A listItem block: release date, title, then the "View More" press-release link.
_LIST_ITEM_RE = re.compile(
    r'<span class="releaseDate">(?P<date>.*?)</span>\s*'
    r'<span class="releaseTitle">(?P<title>.*?)</span>\s*'
    r'<span class="greenListItem"><a href="(?P<href>[^"]+)"',
    re.DOTALL,
)
_MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


def collect(settings: Settings) -> LoadSpec:
    today = date.today()
    rows: list[dict] = []
    with client() as http:
        listing = _get(http, LISTING, "text/html")
        entries = _parse_listing(listing)
        for title, release_type in REPORTS:
            entry = entries.get(title)  # newest release with this exact title
            if entry is None:
                _log.warning("listing had no entry for title", extra={"extras": {"title": title}})
                continue
            listed_date, href = entry
            if listed_date != today:
                _log.info(
                    "latest release for title is not today's; skipping",
                    extra={"extras": {"title": title, "listed_date": listed_date.isoformat()}},
                )
                continue
            rows += _fetch_parse(http, href, release_type, title)

    if not rows:
        _log.info(
            "no S&P Global PMI release due/fresh today; skipping load",
            extra={"extras": {"date": today.isoformat()}},
        )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _fetch_parse(http: httpx.Client, href: str, release_type: str, title: str) -> list[dict]:
    url = href if href.startswith("http") else PRESS_BASE + href
    pdf_bytes = _get_bytes(http, url)
    result = parse_release(pdf_bytes, release_type)
    for w in result.warnings:
        _log.warning("S&P Global PMI consistency check", extra={"extras": {"detail": w}})
    _log.info(
        "S&P Global PMI release parsed",
        extra={
            "extras": {
                "title": title,
                "release_type": result.release_type,
                "release_date": result.release_date.isoformat(),
                "reference_month": result.reference_month.isoformat(),
                "row_count": len(result.rows),
                "url": url,
            }
        },
    )
    return result.rows


def _parse_listing(html: str) -> dict[str, tuple[date, str]]:
    """Map each release title to its newest (date, href). The listing is ordered
    newest-first, so the first occurrence of a title wins."""
    found: dict[str, tuple[date, str]] = {}
    for m in _LIST_ITEM_RE.finditer(html):
        title = m.group("title").strip()
        if title in found:
            continue
        listed = _parse_listed_date(m.group("date"))
        if listed is not None:
            found[title] = (listed, m.group("href"))
    return found


def _parse_listed_date(raw: str) -> date | None:
    # e.g. "May&nbsp;21&nbsp;2026&nbsp;13:45&nbsp;UTC"
    tokens = raw.replace("&nbsp;", " ").split()
    if len(tokens) < 3 or tokens[0] not in _MONTHS:
        return None
    try:
        return date(int(tokens[2]), _MONTHS[tokens[0]], int(tokens[1]))
    except (ValueError, IndexError):
        return None


def _get(http: httpx.Client, url: str, accept: str) -> str:
    def call() -> str:
        response = http.get(url, headers={"Accept": accept})
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _get_bytes(http: httpx.Client, url: str) -> bytes:
    def call() -> bytes:
        response = http.get(url, headers={"Accept": "application/pdf"})
        response.raise_for_status()
        return response.content

    return with_retries(call)
