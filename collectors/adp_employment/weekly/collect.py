import logging
import re
from datetime import date
from html.parser import HTMLParser
from typing import NamedTuple

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "adp_employment.weekly_preliminary"
MEDIA_CENTER_URL = "https://mediacenter.adp.com/workforce-data-releases"
# The landing page is JS-rendered and its static HTML lags (it can omit the most
# recent release — e.g. the 2026-06-23 estimate was live in the RSS feed while
# the static page still topped out at 2026-06-16). The machine-readable RSS feed
# is current, so we discover from it first and fall back to the landing page.
RSS_FEED_URL = "https://mediacenter.adp.com/press-releases?pagetemplate=rss"

MEASURE = "weekly_employment_change_4wk_ma"
DESCRIPTION = (
    "Four-week moving average of net weekly U.S. private-sector employment change "
    "(NER Pulse, seasonally adjusted)"
)
UNITS = "persons"
SEASONAL_ADJUSTMENT = "sa"

# Preliminary release URL pattern. The leading date is the publication date;
# the rest of the slug describes the data week, but we don't parse the slug
# (we use the URL date as vintage and parse the table for week-ending dates).
# The slug excludes '<' so the pattern stays bounded inside RSS <link>...</link>
# elements as well as HTML href="..." attributes.
_PRELIM_URL_RE = re.compile(
    r"https?://mediacenter\.adp\.com/(\d{4})-(\d{2})-(\d{2})-ADP-National-Employment-Report-Preliminary-Estimate-[^\"'\s<]+"
)

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("units", "STRING"),
    bigquery.SchemaField("seasonal_adjustment", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("week_ending", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("vintage_date", "DATE", mode="REQUIRED"),
]


class _Release(NamedTuple):
    url: str
    vintage_date: date


def collect(settings: Settings) -> LoadSpec:
    with client() as http:
        release = _discover_latest_preliminary(http)
        page_html = _fetch_page(http, release.url)

    rows = _parse_release_table(page_html, release.vintage_date)
    _log.info(
        "ADP weekly preliminary parsed",
        extra={
            "extras": {
                "row_count": len(rows),
                "vintage_date": release.vintage_date.isoformat(),
                "url": release.url,
            }
        },
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _discover_latest_preliminary(http: httpx.Client) -> _Release:
    """Newest 'Preliminary Estimate' release across the RSS feed and landing page.

    The static landing page can lag the live RSS feed by a release, so we gather
    candidate URLs from both sources and pick the max-dated one. A single source
    failing (feed format change, page outage) is logged but doesn't abort
    discovery as long as the other yields a release.
    """
    releases: dict[date, str] = {}
    for fetch in (_fetch_rss, _fetch_landing_page):
        try:
            text = fetch(http)
        except Exception as exc:  # noqa: BLE001 - one source down shouldn't break the other
            _log.warning(
                "ADP release-discovery source failed",
                extra={"extras": {"source": fetch.__name__, "error": str(exc)}},
            )
            continue
        for match in _PRELIM_URL_RE.finditer(text):
            year, month, day = (int(g) for g in match.groups())
            try:
                vintage = date(year, month, day)
            except ValueError:
                continue
            releases.setdefault(vintage, match.group(0))

    if not releases:
        raise RuntimeError(
            "could not find a preliminary-estimate release URL in the ADP RSS feed "
            "or on the workforce data releases page"
        )
    latest = max(releases)
    _log.info(
        "discovered latest ADP preliminary release",
        extra={"extras": {"vintage_date": latest.isoformat(), "candidates": len(releases)}},
    )
    return _Release(url=releases[latest], vintage_date=latest)


def _fetch_rss(http: httpx.Client) -> str:
    def call() -> str:
        response = http.get(
            RSS_FEED_URL, headers={"Accept": "application/rss+xml, text/xml, */*"}
        )
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _fetch_landing_page(http: httpx.Client) -> str:
    def call() -> str:
        response = http.get(MEDIA_CENTER_URL, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _fetch_page(http: httpx.Client, url: str) -> str:
    def call() -> str:
        response = http.get(url, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _parse_release_table(html: str, vintage_date: date) -> list[dict]:
    parser = _TableExtractor()
    parser.feed(html)

    target = _select_data_table(parser.tables)
    if target is None:
        raise RuntimeError(
            "preliminary release page lacked a 'Week ending' / 'Four-week moving average' table"
        )

    rows: list[dict] = []
    for row in target[1:]:  # skip header
        if len(row) < 2:
            continue
        week_ending = _parse_table_date(row[0])
        value = _parse_table_value(row[1])
        if week_ending is None or value is None:
            continue
        rows.append(
            {
                "measure": MEASURE,
                "description": DESCRIPTION,
                "units": UNITS,
                "seasonal_adjustment": SEASONAL_ADJUSTMENT,
                "week_ending": week_ending.isoformat(),
                "value": value,
                "vintage_date": vintage_date.isoformat(),
            }
        )
    if not rows:
        raise RuntimeError(
            "found preliminary release table headers but extracted zero data rows"
        )
    return rows


def _select_data_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    """Pick the table whose header row contains 'Week ending' and 'Four-week moving average'."""
    for table in tables:
        if not table:
            continue
        header = " ".join(cell.lower() for cell in table[0])
        if "week ending" in header and "four-week moving average" in header:
            return table
    return None


_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


def _parse_table_date(raw: str) -> date | None:
    """Parse 'M/D/YYYY' or 'M/D/YY' from a cell."""
    match = _DATE_RE.search(raw)
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_table_value(raw: str) -> float | None:
    cleaned = re.sub(r"[,\s]", "", raw)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class _TableExtractor(HTMLParser):
    """Streaming HTML parser that captures every <table> as a list of rows
    (each a list of cell text strings). Nested tables are flattened into the outer
    table; this is fine for our purposes since the press releases use simple tables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            if self._current_table is None:
                self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in ("td", "th") and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._current_cell is not None and self._current_row is not None:
            text = " ".join(part.strip() for part in self._current_cell if part.strip())
            self._current_row.append(text)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0 and self._current_table is not None:
                self.tables.append(self._current_table)
                self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)
