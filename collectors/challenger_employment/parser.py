"""Parse the Challenger, Gray & Christmas monthly job cut report PDF.

The report contains 7 tables with inconsistent layouts. We extract from all of
them and emit rows for three logical destinations: ``monthly`` (Tables 1, 2, 3,
6, 7), ``cut_reasons`` (Table 4), and ``quarterly`` (Table 5).

Each table has its own parsing function because column counts, layouts, and
header conventions vary. Two tables (3 and 7) require positional parsing via
pdfplumber word coordinates to handle two-column layouts and missing-value gaps.
"""

import io
import logging
import re
from dataclasses import dataclass
from datetime import date

import pdfplumber

_log = logging.getLogger(__name__)

_MONTH_NAMES = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}
_MONTH_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_REPORT_MONTH_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b"
)
_RELEASE_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s*(\d{4})",
    re.IGNORECASE,
)



@dataclass(frozen=True)
class ParseResult:
    report_month: date           # first day of the month the report covers (e.g., 2026-03-01)
    release_date: date           # date stamped in the "FOR RELEASE" line
    monthly_rows: list[dict]
    cut_reasons_rows: list[dict]
    quarterly_rows: list[dict]


def parse_report(pdf_bytes: bytes) -> ParseResult:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page1 = pdf.pages[0].extract_text() or ""
        report_month = _parse_report_month(page1)
        release_date = _parse_release_date(page1)

        monthly_rows: list[dict] = []
        cut_reasons_rows: list[dict] = []
        quarterly_rows: list[dict] = []

        for page in pdf.pages:
            text = page.extract_text() or ""
            if "Table 1: EXECUTIVE SUMMARY" in text:
                monthly_rows.extend(_parse_table1_layoffs_total(text, report_month))
            if "Table 2: JOB CUTS BY INDUSTRY" in text:
                monthly_rows.extend(_parse_table2_industry(page, report_month))
            if "Table 3: JOB CUTS BY REGION, STATE" in text:
                monthly_rows.extend(_parse_table3_state(page, report_month))
            if "Table 4: JOB CUTS BY REASON" in text:
                cut_reasons_rows.extend(_parse_table4_reasons(page, report_month))
            if "Table 5: QUARTER BY QUARTER" in text:
                quarterly_rows.extend(_parse_table5_quarterly(page))
            if "Table 6: ANNOUNCED HIRING PLANS" in text:
                monthly_rows.extend(_parse_table6_hiring_total(page, report_month))
            if "Table 7: ANNOUNCED HIRING PLANS" in text:
                monthly_rows.extend(_parse_table7_hiring_industry(page, report_month))

    return ParseResult(
        report_month=report_month,
        release_date=release_date,
        monthly_rows=monthly_rows,
        cut_reasons_rows=cut_reasons_rows,
        quarterly_rows=quarterly_rows,
    )


# ---------- metadata extraction ----------

def _parse_report_month(page1_text: str) -> date:
    """Find 'Month YYYY' near the top of page 1 (after the report title)."""
    after_title = page1_text.split("JOB CUT ANNOUNCEMENT REPORT", 1)
    search_in = after_title[1] if len(after_title) > 1 else page1_text
    match = _REPORT_MONTH_RE.search(search_in)
    if not match:
        raise RuntimeError("could not parse report month from PDF page 1")
    return date(int(match.group(2)), _MONTH_NAMES[match.group(1)], 1)


def _parse_release_date(page1_text: str) -> date:
    """The line 'FOR RELEASE AT ... <Day>, <Month> <DD>, <YYYY>' has the release date."""
    for line in page1_text.splitlines():
        if "FOR RELEASE" in line.upper():
            match = _RELEASE_DATE_RE.search(line)
            if match:
                return date(
                    int(match.group(3)),
                    _MONTH_NAMES[match.group(1).capitalize()],
                    int(match.group(2)),
                )
    raise RuntimeError("could not parse release date from PDF page 1")


# ---------- shared helpers ----------

def _parse_int(raw: str) -> int | None:
    cleaned = raw.replace(",", "").replace("*", "").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


_NUM_TOKEN_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*")


def _group_words_into_rows(words: list[dict], y_tol: float = 3.0) -> list[list[dict]]:
    """Group word dicts (from pdfplumber) into rows by ``top`` y-coordinate."""
    if not words:
        return []
    by_y = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list[list[dict]] = [[by_y[0]]]
    for w in by_y[1:]:
        if abs(w["top"] - rows[-1][0]["top"]) <= y_tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    return rows


def _split_label_and_numbers(row_words: list[dict]) -> tuple[str, list[tuple[float, str]]]:
    """Split a row of word dicts into (label_text, [(x_center, number_token), ...]).

    Number tokens preserve their right-edge x-position for column alignment.
    Adjacent numeric word fragments that form a comma-separated number
    (rare; pdfplumber usually keeps them together) are merged.
    """
    label_parts: list[str] = []
    numbers: list[tuple[float, str]] = []
    for w in row_words:
        text = w["text"]
        if _NUM_TOKEN_RE.fullmatch(text):
            x_center = (w["x0"] + w["x1"]) / 2
            numbers.append((x_center, text))
        elif numbers:
            # Already in numeric region; ignore stray non-numeric (e.g., '*').
            continue
        else:
            label_parts.append(text)
    label = " ".join(label_parts).strip()
    return label, numbers


def _assign_to_columns(
    numbers: list[tuple[float, str]],
    column_centers: list[float],
    tolerance: float = 30.0,
) -> list[str | None]:
    """Place each number into the column whose center is closest, within ``tolerance`` x-units.
    Numbers that don't fall near any column are dropped. Returns a list parallel to
    ``column_centers`` with each cell's string value or None if no number lands there."""
    cells: list[str | None] = [None] * len(column_centers)
    for x_center, token in numbers:
        best_idx = None
        best_dist = tolerance
        for i, c in enumerate(column_centers):
            dist = abs(x_center - c)
            if dist < best_dist and cells[i] is None:
                best_dist = dist
                best_idx = i
        if best_idx is not None:
            cells[best_idx] = token
    return cells


def _column_centers_from_header(
    rows: list[list[dict]], header_keywords: list[str]
) -> tuple[int, list[float]]:
    """Find the row that contains all ``header_keywords`` and return its index and the
    x-centers of the keyword words (in the order keywords were given)."""
    for i, row in enumerate(rows):
        texts = [w["text"] for w in row]
        if all(any(kw == t for t in texts) for kw in header_keywords):
            centers: list[float] = []
            for kw in header_keywords:
                w = next(w for w in row if w["text"] == kw)
                centers.append((w["x0"] + w["x1"]) / 2)
            return i, centers
    raise RuntimeError(f"header row not found; keywords={header_keywords}")


# ---------- Table 1: layoffs total (Month By Month Totals) ----------

def _parse_table1_layoffs_total(page_text: str, report_month: date) -> list[dict]:
    """Extract MONTH BY MONTH TOTALS subsection of Table 1.

    Format: ``Month <cur_year_value> <prior_year_value>`` where the current-year
    value only exists for months <= ``report_month.month``."""
    section = page_text.split("MONTH BY MONTH TOTALS", 1)
    if len(section) < 2:
        raise RuntimeError("MONTH BY MONTH TOTALS section not found in Table 1")
    after = section[1]
    # End at "TOTAL" or "LAYOFF LOCATION" — whichever comes first.
    end_idx = min(
        (i for i in (after.find("LAYOFF LOCATION"), after.find("\nTOTAL")) if i != -1),
        default=len(after),
    )
    block = after[:end_idx]

    rows: list[dict] = []
    cur_year, prior_year = report_month.year, report_month.year - 1
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(
            r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(.+)$",
            line,
        )
        if not match:
            continue
        month_name = match.group(1)
        month_num = _MONTH_NAMES[month_name]
        numbers = _NUM_TOKEN_RE.findall(match.group(2))
        if not numbers:
            continue

        # Only months <= report_month have a current-year value.
        if month_num <= report_month.month:
            if len(numbers) >= 2:
                rows.append(_layoff_total_row(cur_year, month_num, numbers[0]))
                rows.append(_layoff_total_row(prior_year, month_num, numbers[1]))
            elif len(numbers) == 1:
                rows.append(_layoff_total_row(cur_year, month_num, numbers[0]))
        else:
            # Months past the report month only have prior year filled in.
            rows.append(_layoff_total_row(prior_year, month_num, numbers[0]))
    return rows


def _layoff_total_row(year: int, month: int, raw_value: str) -> dict:
    return _monthly_row("layoffs", "total", None, None, year, month, raw_value)


# ---------- Table 2: layoffs by industry ----------

def _parse_table2_industry(page, report_month: date) -> list[dict]:
    """Columns: ``Industry | 25-Mar | 26-Feb | 26-Mar | YTD 2025 | YTD 2026``.
    Keep the three monthly columns; drop YTD."""
    words = page.extract_words()
    rows = _group_words_into_rows(words)

    # Header row contains a token like '25-Mar' / '26-Feb' / '26-Mar'.
    yy_mon_re = re.compile(r"^\d{2}-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$")
    header_idx = None
    monthly_columns: list[tuple[date, float]] = []  # (observation_month, x_center)
    for i, row in enumerate(rows):
        yy_mon_words = [w for w in row if yy_mon_re.match(w["text"])]
        if len(yy_mon_words) >= 2:
            header_idx = i
            for w in yy_mon_words:
                obs = _parse_yy_mon(w["text"], report_month.year)
                monthly_columns.append((obs, (w["x0"] + w["x1"]) / 2))
            break
    if header_idx is None:
        raise RuntimeError("Table 2 header row (with YY-Mon columns) not found")

    centers = [c for _, c in monthly_columns]
    obs_months = [m for m, _ in monthly_columns]

    out: list[dict] = []
    for row in rows[header_idx + 1:]:
        label, numbers = _split_label_and_numbers(row)
        if not label or label.upper().startswith("TOTAL") or label.startswith("Source:"):
            continue
        cells = _assign_to_columns(numbers, centers)
        for cell, obs in zip(cells, obs_months, strict=False):
            if cell is None:
                continue
            out.append(_monthly_row("layoffs", "industry", label, None, obs.year, obs.month, cell))
    return out


# ---------- Table 3: layoffs by state ----------

def _parse_table3_state(page, report_month: date) -> list[dict]:
    """Two-column layout: EAST/MIDWEST/SOUTH stack on the left side, WEST occupies
    the right side. The split between sides is the x0 of the 'WEST' header word,
    located dynamically since column widths vary release-to-release. Each
    sub-table has columns ``State | Mar-26 | YTD 2026 | YTD 2025``; we keep only
    the Mar-26 monthly value."""
    words = page.extract_words()
    split_x = _find_west_header_x(words)

    left_words = [w for w in words if w["x0"] < split_x]
    right_words = [w for w in words if w["x0"] >= split_x]

    out: list[dict] = []
    out.extend(_parse_table3_side(left_words, report_month, ("EAST", "MIDWEST", "SOUTH")))
    out.extend(_parse_table3_side(right_words, report_month, ("WEST",)))
    return out


def _find_west_header_x(words: list[dict]) -> float:
    """Return the x0 of the 'WEST' region header word."""
    for w in words:
        if w["text"] == "WEST":
            # Pad slightly to the left so the WEST header word itself is included.
            return w["x0"] - 5.0
    raise RuntimeError("Table 3: 'WEST' region header not found on page")


def _parse_table3_side(
    words: list[dict], report_month: date, region_order: tuple[str, ...]
) -> list[dict]:
    rows = _group_words_into_rows(words)

    # Find each region header. The header line is the row whose first word equals a region name.
    region_starts: dict[str, int] = {}
    monthly_x: float | None = None  # x-center of the 'Mar-26' column header
    for i, row in enumerate(rows):
        first_text = row[0]["text"] if row else ""
        if first_text in region_order and first_text not in region_starts:
            region_starts[first_text] = i
            # Capture the monthly column position from the first region's header.
            if monthly_x is None:
                for w in row:
                    if w["text"].lower().startswith("mar-"):
                        monthly_x = (w["x0"] + w["x1"]) / 2
                        break
    if monthly_x is None:
        raise RuntimeError(f"Table 3 monthly column header not located; regions={region_order}")

    # Build (region_name, start_idx, end_idx) ranges in document order.
    sorted_regions = sorted(region_starts.items(), key=lambda kv: kv[1])
    ranges: list[tuple[str, int, int]] = []
    for j, (region, start) in enumerate(sorted_regions):
        end = sorted_regions[j + 1][1] if j + 1 < len(sorted_regions) else len(rows)
        ranges.append((region, start + 1, end))

    out: list[dict] = []
    for region, start, end in ranges:
        for row in rows[start:end]:
            label, numbers = _split_label_and_numbers(row)
            if not label or label.upper().startswith("TOTAL"):
                continue
            cells = _assign_to_columns(numbers, [monthly_x], tolerance=40.0)
            cell = cells[0]
            if cell is None:
                continue
            out.append(
                _monthly_row(
                    "layoffs", "state", label, region,
                    report_month.year, report_month.month, cell,
                )
            )
    return out


# ---------- Table 4: cut reasons ----------

def _parse_table4_reasons(page, report_month: date) -> list[dict]:
    """Columns: ``Reason | Mar-26 | YTD 2026``. Keep only Mar-26."""
    words = page.extract_words()
    rows = _group_words_into_rows(words)

    monthly_x: float | None = None
    header_idx: int | None = None
    for i, row in enumerate(rows):
        for w in row:
            if w["text"].lower().startswith("mar-"):
                monthly_x = (w["x0"] + w["x1"]) / 2
                header_idx = i
                break
        if header_idx is not None:
            break
    if header_idx is None or monthly_x is None:
        raise RuntimeError("Table 4 header row (Mar-YY column) not found")

    out: list[dict] = []
    for row in rows[header_idx + 1:]:
        label, numbers = _split_label_and_numbers(row)
        if not label or label.upper() == "TOTAL" or label.startswith("Source:"):
            continue
        cells = _assign_to_columns(numbers, [monthly_x], tolerance=40.0)
        cell = cells[0]
        if cell is None:
            continue
        out.append(
            {
                "reason": label,
                "observation_month": date(report_month.year, report_month.month, 1).isoformat(),
                "value": float(cell.replace(",", "")),
            }
        )
    return out


# ---------- Table 5: quarterly ----------

def _parse_table5_quarterly(page) -> list[dict]:
    """Columns: ``Year | Q1 | Q2 | Q3 | Q4 | TOTAL``. Drop TOTAL; keep individual quarters.
    Rows starting with non-year tokens (AVG, Source) are skipped. Years can have a
    trailing '*' (footnote) which we strip."""
    words = page.extract_words()
    rows = _group_words_into_rows(words)

    header_idx, centers = _column_centers_from_header(rows, ["Q1", "Q2", "Q3", "Q4"])

    out: list[dict] = []
    for row in rows[header_idx + 1:]:
        if not row:
            continue
        first = row[0]["text"].rstrip("*")
        if not first.isdigit():
            continue
        year = int(first)
        # Reuse split-by-label by treating the year word as the label.
        _, numbers = _split_label_and_numbers(row)
        cells = _assign_to_columns(numbers, centers, tolerance=40.0)
        for q_idx, cell in enumerate(cells):
            if cell is None:
                continue
            quarter_start_month = q_idx * 3 + 1
            out.append(
                {
                    "quarter_start": date(year, quarter_start_month, 1).isoformat(),
                    "value": float(cell.replace(",", "")),
                }
            )
    return out


# ---------- Table 6: hiring totals (10 years × 12 months) ----------

def _parse_table6_hiring_total(page, report_month: date) -> list[dict]:
    """Columns: ``Month | <year_n> | <year_n-1> | ... | <year_n-9>``.

    Skip TOTAL/YTD/Monthly Average rows."""
    words = page.extract_words()
    rows = _group_words_into_rows(words)

    # The header has 10 four-digit-year tokens.
    year_re = re.compile(r"^\d{4}$")
    header_idx = None
    year_columns: list[tuple[int, float]] = []  # (year, x_center)
    for i, row in enumerate(rows):
        years_in_row = [w for w in row if year_re.match(w["text"])]
        if len(years_in_row) >= 8:  # tolerate variation; 10 expected
            header_idx = i
            for w in years_in_row:
                year_columns.append((int(w["text"]), (w["x0"] + w["x1"]) / 2))
            break
    if header_idx is None:
        raise RuntimeError("Table 6 header row (10 years) not found")

    centers = [c for _, c in year_columns]
    years = [y for y, _ in year_columns]

    out: list[dict] = []
    skip_labels = {"TOTAL", "YTD", "MONTHLY AVERAGE", "MONTHLY", "AVERAGE"}
    for row in rows[header_idx + 1:]:
        label, numbers = _split_label_and_numbers(row)
        if not label:
            continue
        label_clean = label.upper().strip()
        if label_clean in skip_labels:
            continue
        if label not in _MONTH_NAMES:
            # Skip any unexpected row (e.g., section markers or stray text).
            continue
        month_num = _MONTH_NAMES[label]
        cells = _assign_to_columns(numbers, centers, tolerance=30.0)
        for cell, year in zip(cells, years, strict=False):
            if cell is None:
                continue
            out.append(_monthly_row("hiring", "total", None, None, year, month_num, cell))
    return out


# ---------- Table 7: hiring by industry ----------

def _parse_table7_hiring_industry(page, report_month: date) -> list[dict]:
    """Columns: ``Industry | 26-Mar | YTD 2026 | YTD 2025`` (per the March 2026 sample).
    Keep only the 26-Mar monthly column."""
    words = page.extract_words()
    rows = _group_words_into_rows(words)

    monthly_x: float | None = None
    header_idx: int | None = None
    for i, row in enumerate(rows):
        for w in row:
            text = w["text"]
            # Either '26-Mar' (Table 7 March 2026) or 'Mar-26' (other formats); accept both.
            if re.match(r"^\d{2}-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$", text) or \
               re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2}$", text):
                monthly_x = (w["x0"] + w["x1"]) / 2
                header_idx = i
                break
        if header_idx is not None:
            break
    if header_idx is None or monthly_x is None:
        raise RuntimeError("Table 7 header row (monthly column) not found")

    out: list[dict] = []
    for row in rows[header_idx + 1:]:
        label, numbers = _split_label_and_numbers(row)
        if not label or label.upper() == "TOTAL" or label.startswith("Source:") \
                or label.upper().startswith("INDUSTRY"):
            continue
        cells = _assign_to_columns(numbers, [monthly_x], tolerance=40.0)
        cell = cells[0]
        if cell is None:
            continue
        out.append(
            _monthly_row(
                "hiring", "industry", label, None,
                report_month.year, report_month.month, cell,
            )
        )
    return out


# ---------- shared row builders / parsers ----------

def _monthly_row(
    series: str,
    breakdown: str,
    category: str | None,
    region: str | None,
    year: int,
    month: int,
    raw_value: str,
) -> dict:
    return {
        "series": series,
        "breakdown": breakdown,
        "category": category,
        "region": region,
        "observation_month": date(year, month, 1).isoformat(),
        "value": float(raw_value.replace(",", "")),
    }


def _parse_yy_mon(text: str, current_year: int) -> date:
    """Parse '25-Mar' or '26-Feb' to a date. Year inferred from current_year's century."""
    yy, mon = text.split("-")
    century = (current_year // 100) * 100
    year = century + int(yy)
    return date(year, _MONTH_ABBR[mon], 1)
