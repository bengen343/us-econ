import logging
import re
from datetime import date, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

URL = "https://gasprices.aaa.com/"
TABLE = "aaa_gasoline.daily"

# Order matches the column headers in the National Average Gas Prices table.
GRADES = ("Regular", "Mid-Grade", "Premium", "Diesel", "E85")

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("grade", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("price_usd_per_gallon", "FLOAT64"),
]


def collect(settings: Settings) -> LoadSpec:
    with client() as http:
        html = _fetch_page(http, URL)

    observation_date = datetime.now(ZoneInfo("America/Denver")).date()
    rows = _parse_current_avg(html, observation_date)
    _log.info(
        "AAA national average gas prices parsed",
        extra={
            "extras": {
                "row_count": len(rows),
                "observation_date": observation_date.isoformat(),
                "url": URL,
            }
        },
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _fetch_page(http: httpx.Client, url: str) -> str:
    def call() -> str:
        response = http.get(url, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _parse_current_avg(html: str, observation_date: date) -> list[dict]:
    parser = _TableExtractor()
    parser.feed(html)

    target = _select_national_average_table(parser.tables)
    if target is None:
        raise RuntimeError("could not locate the National Average Gas Prices table")

    column_index: dict[str, int] = {}
    for idx, cell in enumerate(target[0]):
        for grade in GRADES:
            if cell.strip().lower() == grade.lower():
                column_index[grade] = idx
                break
    missing = [g for g in GRADES if g not in column_index]
    if missing:
        raise RuntimeError(f"AAA table header missing grade columns: {missing}")

    current_row: list[str] | None = None
    for row in target[1:]:
        if row and row[0].strip().lower().startswith("current avg"):
            current_row = row
            break
    if current_row is None:
        raise RuntimeError("AAA table did not contain a 'Current Avg.' row")

    rows: list[dict] = []
    for grade in GRADES:
        col = column_index[grade]
        if col >= len(current_row):
            continue
        rows.append(
            {
                "observation_date": observation_date.isoformat(),
                "grade": grade,
                "price_usd_per_gallon": _parse_price(current_row[col]),
            }
        )
    return rows


def _select_national_average_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    """Pick the table whose header row contains the gas grade names."""
    for table in tables:
        if len(table) < 2:
            continue
        header = " ".join(cell.lower() for cell in table[0])
        if all(grade.lower() in header for grade in ("Regular", "Premium", "Diesel")):
            return table
    return None


_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _parse_price(raw: str) -> float | None:
    match = _PRICE_RE.search(raw)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


class _TableExtractor(HTMLParser):
    """Streaming HTML parser that captures every <table> as a list of rows
    (each a list of cell text strings). Nested tables are flattened into the outer
    table; fine here since gasprices.aaa.com uses a single flat table."""

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
