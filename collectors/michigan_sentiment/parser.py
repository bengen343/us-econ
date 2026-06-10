"""Parse the University of Michigan Surveys of Consumers homepage + data CSVs.

The SCA homepage (sca.isr.umich.edu) always shows the latest release: an
``<h1>`` of the form "Preliminary Results for June 2026" / "Final Results for
May 2026" and a ``front_table`` whose rows are the three indexes with the
current month's value in the first (``em``-classed) value column. Unlike ISM,
the Michigan survey for month M is released DURING month M -- preliminary
mid-month (~2nd Friday), final at month end (~4th Friday), both Fridays
10:00 ET.

The site also publishes the full *final* history as CSVs
(``files/tbmics.csv`` -- ICS; ``files/tbmiccice.csv`` -- ICC + ICE), which we
re-ingest every run so a missed final Friday self-heals. Preliminary values
exist only on the homepage during prelim weeks (and in Wayback snapshots) --
a missed preliminary capture does NOT self-heal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTHS_ALT = "|".join(_MONTHS)
_H1_RE = re.compile(
    rf"(Preliminary|Final)\s+Results\s+for\s+({_MONTHS_ALT})\s+(\d{{4}})", re.IGNORECASE
)

# Homepage table label -> our measure slug.
MEASURES = {
    "index of consumer sentiment": "sentiment",
    "current economic conditions": "current_conditions",
    "index of consumer expectations": "expectations",
}


@dataclass
class HomepageResult:
    release_type: str  # "preliminary" | "final"
    observation_month: date
    values: dict[str, float]  # measure slug -> index value
    warnings: list[str] = field(default_factory=list)


class _FrontTable(HTMLParser):
    """Collect the cell texts of every row of ``<table id="front_table">``."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_table = False
        self._in_cell = False
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table" and dict(attrs).get("id") == "front_table":
            self._in_table = True
        elif self._in_table and tag == "tr":
            self.rows.append([])
        elif self._in_table and tag == "td":
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif self._in_table and tag == "td":
            self._in_cell = False
            self.rows[-1].append(" ".join("".join(self._cell).split()))

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell.append(data)


def parse_homepage(html: str) -> HomepageResult:
    """Release type + month from the headline, index values from front_table."""
    match = _H1_RE.search(html)
    if match is None:
        raise ValueError("no 'Preliminary|Final Results for <Month> <Year>' headline found")
    release_type = match.group(1).lower()
    month = date(int(match.group(3)), _MONTHS[match.group(2).lower()], 1)

    table = _FrontTable()
    table.feed(html)
    values: dict[str, float] = {}
    warnings: list[str] = []
    for row in table.rows:
        if not row:
            continue
        measure = MEASURES.get(row[0].lower())
        if measure is None:
            continue
        try:
            values[measure] = float(row[1])
        except (IndexError, ValueError):
            warnings.append(f"unparseable value for {row[0]!r}: {row[1:2]!r}")

    missing = sorted(set(MEASURES.values()) - set(values))
    if missing:
        warnings.append(f"measures missing from front_table: {missing}")
    return HomepageResult(release_type, month, values, warnings)


def parse_csv_months(text: str) -> list[tuple[date, list[float | None]]]:
    """Parse an SCA data CSV (``Month,YYYY,<value columns...>``) into
    (month, [values...]) rows; blank cells become None."""
    out: list[tuple[date, list[float | None]]] = []
    for line in text.strip().splitlines()[1:]:
        cells = [c.strip() for c in line.split(",")]
        month_name = cells[0].lower()
        if month_name not in _MONTHS or len(cells) < 3:
            continue
        month = date(int(cells[1]), _MONTHS[month_name], 1)
        out.append((month, [float(c) if c else None for c in cells[2:]]))
    return out
