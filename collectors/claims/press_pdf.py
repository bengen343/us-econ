import io
import logging
import re
from datetime import date
from typing import NamedTuple

import httpx
import pdfplumber

from collectors.claims.series import MEASURES_BY_KEY
from collectors.common.http import with_retries

PRESS_PDF_URL = "https://www.dol.gov/ui/data.pdf"
SOURCE = "dol_press_pdf"

_log = logging.getLogger(__name__)

_RELEASE_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})",
)
_MONTH_NAMES = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}

# Maps a row's leading label to (measure, seasonal_adjustment). The "4-Wk
# Moving Average (SA)" label is context-sensitive (appears in both the initial
# claims and insured unemployment tables) and is resolved at parse time.
_ROW_LABELS: dict[str, tuple[str, str]] = {
    "Initial Claims (SA)":              ("initial_claims",   "sa"),
    "Initial Claims (NSA)":             ("initial_claims",   "nsa"),
    "Insured Unemployment (SA)":        ("continued_claims", "sa"),
    "Insured Unemployment (NSA)":       ("continued_claims", "nsa"),
    "Insured Unemployment Rate (SA)":   ("iur",              "sa"),
    "Insured Unemployment Rate (NSA)":  ("iur",              "nsa"),
}


class FetchResult(NamedTuple):
    vintage_date: date | None
    rows: list[dict]


def fetch(http: httpx.Client) -> FetchResult:
    """Best-effort: download and parse the press PDF. Returns empty rows on any failure."""
    try:
        pdf_bytes = _download(http)
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            release = _find_release_date(pdf)
            rows = _parse_national_tables(pdf, release)
        _log.info(
            "press PDF parsed",
            extra={"extras": {"vintage_date": release.isoformat() if release else None,
                              "row_count": len(rows)}},
        )
        return FetchResult(vintage_date=release, rows=rows)
    except Exception as exc:
        _log.exception("press PDF fetch/parse failed; continuing without PDF rows: %s", exc)
        return FetchResult(vintage_date=None, rows=[])


def _download(http: httpx.Client) -> bytes:
    def call() -> bytes:
        response = http.get(PRESS_PDF_URL, headers={"Accept": "application/pdf"})
        response.raise_for_status()
        return response.content

    return with_retries(call)


def _find_release_date(pdf: "pdfplumber.PDF") -> date:
    page1 = pdf.pages[0].extract_text() or ""
    # The release date appears after "EMBARGOED UNTIL ... <time> ... <Day>, <Month> <Day>, <Year>".
    for line in page1.splitlines():
        if "EMBARGOED" in line.upper():
            continue
        m = _RELEASE_DATE_RE.search(line)
        if m:
            month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
            return date(year, _MONTH_NAMES[month], day)
    # Fall back: search the whole page1 text for the first MMM DD, YYYY token.
    m = _RELEASE_DATE_RE.search(page1)
    if not m:
        raise RuntimeError("press PDF page 1 did not contain a parseable release date")
    return date(int(m.group(3)), _MONTH_NAMES[m.group(1)], int(m.group(2)))


def _parse_national_tables(pdf: "pdfplumber.PDF", release: date) -> list[dict]:
    """Find page with the regular-state-programs tables and extract per-week rows."""
    target_text: str | None = None
    for page in pdf.pages:
        text = page.extract_text() or ""
        if "UNEMPLOYMENT INSURANCE DATA FOR REGULAR STATE PROGRAMS" in text:
            target_text = text
            break
    if target_text is None:
        raise RuntimeError("press PDF did not contain national headline tables page")

    lines = [_strip_footnote_markers(ln.rstrip()) for ln in target_text.splitlines() if ln.strip()]
    rows: list[dict] = []
    current_week_endings: list[date] = []

    for line in lines:
        if line.startswith("WEEK ENDING"):
            current_week_endings = _parse_week_ending_header(line, release)
            continue
        if not current_week_endings:
            continue

        parsed = _parse_data_row(line, current_week_endings, release)
        rows.extend(parsed)
    return rows


def _strip_footnote_markers(line: str) -> str:
    """The press PDF tags some labels with footnote digits, e.g. 'Rate (SA)2'.
    Strip those so label matching and the numeric tokenizer don't trip over them."""
    return re.sub(r"\)(\d+)(\s)", r")\2", line)


def _parse_week_ending_header(line: str, release: date) -> list[date]:
    """
    Parse a 'WEEK ENDING April 25 April 18 Change April 11 Prior Year1' header into
    the 3 informative week-ending dates (skip 'Change' and 'Prior Year').
    """
    body = line[len("WEEK ENDING"):].strip()
    tokens = body.split()
    months = set(_MONTH_NAMES.keys())
    pairs: list[date] = []
    i = 0
    while i < len(tokens):
        if tokens[i] in months and i + 1 < len(tokens):
            day_token = tokens[i + 1].rstrip(",")
            try:
                day = int(day_token)
            except ValueError:
                i += 1
                continue
            pairs.append(_resolve_week_ending(tokens[i], day, release))
            i += 2
        else:
            i += 1
    # Expect 4 dates (advance, prior, prior-prior, year-ago). Drop the year-ago
    # (last one) — it's a duplicate vintage of historical data we already have.
    if len(pairs) >= 4:
        return pairs[:3]
    return pairs[:3]


def _resolve_week_ending(month_name: str, day: int, release: date) -> date:
    """Resolve a 'Month Day' header (no year printed) using the release date as anchor."""
    month = _MONTH_NAMES[month_name]
    candidate = date(release.year, month, day)
    # Data weeks are always before the release; if we resolved into the future,
    # the release year wraps (e.g., December dates printed in a January release).
    if candidate > release:
        candidate = date(release.year - 1, month, day)
    return candidate


_NUMERIC_TOKEN_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?")


def _parse_data_row(line: str, week_endings: list[date], release: date) -> list[dict]:
    """
    Map a row like 'Initial Claims (SA) 189,000 215,000 -26,000 208,000 239,000'
    to one row per (measure, sa, week_ending). Skips '4-Wk Moving Average' rows
    (the SA4WK_MA flavor exists in the doleta XML; press PDF rows are redundant).
    Skips columns we don't store: 'Change' (col 2) and 'Prior Year' (col 4).
    """
    label_match = next(
        ((label, key) for label, key in _ROW_LABELS.items() if line.startswith(label)),
        None,
    )
    if not label_match:
        return []
    label, (measure, sa) = label_match
    value_str = line[len(label):]
    tokens = _NUMERIC_TOKEN_RE.findall(value_str)
    # Expect 5 columns: advance, prior, change, prior-prior, year-ago.
    if len(tokens) < 4:
        return []

    # Columns to store: indices 0 (advance), 1 (prior wk revised), 3 (older revised).
    # Skip 2 (Change, redundant) and 4 (Prior Year, duplicate vintage).
    keepers = [(tokens[0], week_endings[0]),
               (tokens[1], week_endings[1]),
               (tokens[3], week_endings[2])]

    rows: list[dict] = []
    for raw, we in keepers:
        value = _parse_value(raw)
        if value is None:
            continue
        meta = MEASURES_BY_KEY[measure]
        rows.append({
            "series_id": f"press.us.{measure}.{sa}",
            "source": SOURCE,
            "level": "national",
            "area": "US",
            "measure": measure,
            "seasonal_adjustment": sa,
            "description": meta.description,
            "units": meta.units,
            "week_ending": we.isoformat(),
            "vintage_date": release.isoformat(),
            "value": value,
        })
    return rows


def _parse_value(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").replace("%", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
