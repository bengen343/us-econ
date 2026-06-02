"""Parse an ISM Report On Business press release (PR Newswire).

ISM's own site (ismworld.org) gates the reports behind an SSO login, but ISM
distributes each monthly Report On Business — including the full "... at a
Glance" diffusion-index table — publicly via PR Newswire. We parse that table.

The same parser serves both reports (Manufacturing and Services); their tables
share the layout ``[Index, this-month, last-month, % point change, direction,
rate of change, trend]`` but differ in which index rows appear (e.g.
Manufacturing has "Production", Services has "Business Activity"). We therefore
parse whatever numeric index rows are present rather than hard-coding them, and
capture every series for posterity.

Per index we record the current reference (survey) month's value and the stated
prior-month value, so a missed run self-heals from the next release. Note: an
ISM report is *named* for the survey month but released the following month
(e.g. the "May 2026" report drops the first business day of June), so the
reference month comes from the title, not the release date.
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
# Reference (survey) month from the headline, e.g. "May 2026 ISM Manufacturing PMI Report".
_REF_RE = re.compile(rf"({_MONTHS_ALT})\s+(\d{{4}})\s+ISM", re.IGNORECASE)
_NUM_RE = re.compile(r"^[+-]?\d{1,3}(?:\.\d+)?$")


@dataclass
class ParseResult:
    report: str  # "manufacturing" | "services"
    reference_month: date
    rows: list[dict]
    warnings: list[str] = field(default_factory=list)


def _slug(label: str) -> str:
    label = label.replace("®", " ")  # drop ®
    low = label.lower()
    if re.search(r"\bpmi\b", low):
        return "pmi"  # headline index, aligned across both reports
    if "business activity" in low:
        return "business_activity"  # Services headline activity ("Business Activity/Production")
    return re.sub(r"[^a-z0-9]+", "_", low).strip("_")


def _prior_month(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def _to_float(cell: str) -> float | None:
    cell = cell.strip().replace("%", "")
    return float(cell) if _NUM_RE.match(cell) else None


def parse_report(html: str, report: str) -> ParseResult:
    text = _TextExtractor.to_text(html)
    rm = _REF_RE.search(text)
    if rm is None:
        raise RuntimeError(
            "could not find reference month (e.g. 'May 2026 ISM ...') in the release"
        )
    reference_month = date(int(rm.group(2)), _MONTHS[rm.group(1).lower()], 1)
    prior_month = _prior_month(reference_month)

    tables = _TableExtractor.extract(html)
    glance = _select_glance_table(tables)
    if glance is None:
        raise RuntimeError("could not locate the 'at a Glance' index table in the release")

    rows: list[dict] = []
    seen: set[str] = set()
    for cells in glance:
        if len(cells) < 2:
            continue
        cur = _to_float(cells[1])
        if cur is None:  # header row or a summary row with no index value
            continue
        measure = _slug(cells[0])
        if not measure or measure in seen:
            continue
        seen.add(measure)
        rows.append(_row(report, measure, reference_month, reference_month, cur))
        prior = _to_float(cells[2]) if len(cells) > 2 else None
        if prior is not None:
            rows.append(_row(report, measure, prior_month, reference_month, prior))

    if not any(r["measure"] == "pmi" for r in rows):
        raise RuntimeError(f"no headline PMI parsed from {report} report; layout changed")

    return ParseResult(report, reference_month, rows, _validate(rows))


def _row(report: str, measure: str, obs: date, ref: date, value: float) -> dict:
    return {
        "report": report,
        "measure": measure,
        "observation_month": obs.isoformat(),
        "value": value,
        "units": "index",
        "release_month": ref.isoformat(),
    }


def _select_glance_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    """The at-a-glance table has both a headline PMI row and an Employment row."""
    for table in tables:
        labels = " | ".join((row[0] if row else "").lower() for row in table)
        if "employment" in labels and "pmi" in labels:
            return table
    return None


def _validate(rows: list[dict]) -> list[str]:
    warnings: list[str] = []
    for r in rows:
        if not (0.0 <= r["value"] <= 100.0):
            warnings.append(
                f"{r['report']}.{r['measure']} {r['observation_month']} = "
                f"{r['value']} outside [0,100]"
            )
    return warnings


class _TextExtractor(HTMLParser):
    """Flatten HTML to whitespace-collapsed text (drops script/style)."""

    @classmethod
    def to_text(cls, html: str) -> str:
        p = cls()
        p.feed(html)
        return re.sub(r"\s+", " ", " ".join(p._parts))

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data)


class _TableExtractor(HTMLParser):
    """Capture every <table> as a list of rows (each a list of cell-text strings)."""

    @classmethod
    def extract(cls, html: str) -> list[list[list[str]]]:
        p = cls()
        p.feed(html)
        return p.tables

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._depth += 1
            if self._table is None:
                self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join(p.strip() for p in self._cell if p.strip()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            if self._depth == 0 and self._table is not None:
                self.tables.append(self._table)
                self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
