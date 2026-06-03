"""Parse an S&P Global US PMI press release (PDF, pmi.spglobal.com).

S&P Global publishes the monthly US PMI releases as PDFs on the public PMI site
(pmi.spglobal.com/Public/Home/PressRelease/<hash>). Unlike ISM, the figures are
not laid out in a clean "at a Glance" table — they are stated in prose and (for
the flash) a short bulleted summary box. We therefore extract the headline
diffusion-index values with targeted regexes rather than table parsing.

Two release types feed this collector, both naming the S&P Global *US Services
Business Activity Index* (the headline "Services PMI"):

  * flash  — the preliminary estimate, published ~21st of the survey month from
             ~80-90% of responses. The leading signal: it lands ~2 weeks before
             ISM Services for the same survey month. Its summary box also gives
             the Composite Output Index and both Manufacturing headline indices,
             which we capture too (cheap, and useful as extra forecast inputs).
             Only the current (flash) month is taken — the bullet's "(April: ...)"
             parenthetical is the prior month's *final* (already out by flash day),
             not a flash reading, so it's deliberately not emitted.
  * final  — the full release, published the 3rd of the following month (per the
             release's own methodology note), confirming the survey month. Only
             the Services Business Activity Index is extracted; the composite is
             intentionally skipped because the PDF's two-column layout interleaves
             the contact box through "...up [column break] from <prior>", making
             a robust prior-value extraction impossible. The final does restate the
             immediately-prior month's value, so a missed final self-heals next
             month; a missed flash does not (only its own release carries it).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

import pdfplumber

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

# Publication date from the embargo line, e.g. "... 5 May 2026 / 1345 UTC ...".
_EMBARGO_DATE_RE = re.compile(rf"(\d{{1,2}})\s+({_MONTHS_ALT})\s+(\d{{4}})", re.IGNORECASE)

# A headline index value, e.g. "50.9". Always reported to one decimal place, which
# lets us skip the integer chart-axis tick labels (70, 65, ..., 12, 9, ...) that
# pdfplumber interleaves into the text from the embedded sparkline charts.
_DEC = r"([0-9]{1,3}\.[0-9]+)"

# Flash summary-box bullets, matched leniently to survive format drift across
# vintages: an optional footnote marker "(2)", a ":" or " at " separator, the
# optional word "PMI". We anchor on the label, take the first decimal as the
# current value ([^(]* skips chart-axis ticks; the footnote's "(" is consumed by
# the separator first), then OPTIONALLY a trailing "(<Month>: <prior>)". The prior
# is best-effort — pdfplumber doesn't always keep it adjacent to its label — and
# isn't needed for correctness since each month's own release carries its value.
_FLASH_BULLETS = [
    (r"Flash US Composite (?:PMI )?Output Index", "composite", "output"),
    (r"Flash US Services (?:PMI )?Business Activity Index", "services", "business_activity"),
    (r"Flash US Manufacturing Output Index", "manufacturing", "output"),
    (r"Flash US Manufacturing PMI", "manufacturing", "pmi"),
]
_SEP = r"(?:\(\d\))?\s*(?::|\bat\b)"

# Final-release headline, stated in prose with a direction-dependent verb and
# variable framing, e.g. "...Index registered 51.0 in April following 49.8 in
# March", "...Index posted 54.5 in August, down from 55.7 in July", "...Index rose
# to a seven-month high of 55.7 in July, up from 52.9 in June", or split across
# sentences "...Index registered 52.5 in December ... was down from 54.1 in
# November". We extract the current value by anchoring on the headline "Index"
# followed by bounded word/space junk (the verb framing and any two-column splice
# like "Market Intelligence"/"Comment") then "<v> in <Month>"; the first such hit
# leads the release. The prior value is then sought in a bounded window after it,
# behind a direction connector, and accepted only if it resolves to the month
# immediately before the current one. Best-effort: if the current value can't be
# matched the final is skipped (see _parse_final).
_FINAL_CURRENT_RE = re.compile(
    rf"Index[\w\s,'’\-]{{0,60}}?{_DEC}\s+in\s+({_MONTHS_ALT})", re.IGNORECASE
)
_FINAL_PRIOR_RE = re.compile(
    rf"(?:following|(?:up|down)\s+from|from)\s+{_DEC}\s+in\s+({_MONTHS_ALT})", re.IGNORECASE
)
# Fallback for the month-first framing where the value trails the month, e.g.
# "...Index fell in February, decreasing to 51.7 from 52.7 ...": month, then
# "to <current> from <prior>". Only tried when _FINAL_CURRENT_RE misses; the
# prior is accepted under the same month-before-current guard in _parse_final.
_FINAL_ALT_RE = re.compile(
    rf"Index[\w\s,'’\-]{{0,40}}?in\s+({_MONTHS_ALT})[\w\s,'’\-]{{0,40}}?"
    rf"to\s+{_DEC}\s+from\s+{_DEC}",
    re.IGNORECASE,
)


@dataclass
class ParseResult:
    release_type: str  # "flash" | "final"
    release_date: date
    reference_month: date  # the survey (current) month named by the release
    rows: list[dict]
    warnings: list[str] = field(default_factory=list)


def extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def parse_release(pdf_bytes: bytes, release_type: str) -> ParseResult:
    if release_type not in ("flash", "final"):
        raise ValueError(f"unknown release_type: {release_type!r}")
    text = extract_text(pdf_bytes)
    release_date = _find_release_date(text)

    if release_type == "flash":
        rows, ref, warnings = _parse_flash(text, release_date)
        # The flash bullets are the structured headline figures; if the Services
        # Business Activity Index isn't among them the layout has changed and we
        # should fail loudly rather than load a flash with no services reading.
        if not any(r["report"] == "services" and r["measure"] == "business_activity" for r in rows):
            raise RuntimeError(
                "no Services Business Activity Index parsed from flash release; "
                "layout may have changed"
            )
    else:
        rows, ref, warnings = _parse_final(text, release_date)
    return ParseResult(release_type, release_date, ref, rows, warnings)


def _parse_flash(text: str, release_date: date) -> tuple[list[dict], date, list[str]]:
    # Flash is published within its survey month, so the survey month is the
    # release month. We take only the current (flash) value per bullet: the
    # parenthetical "(April: 51.0)" is NOT a flash reading — it's the prior
    # month's final (which publishes ~3rd, before this ~21st flash), so emitting
    # it as release_type="flash" would clobber that month's true flash via the
    # latest-ingested_at dedup. The prior month's own flash carries its value.
    current = date(release_date.year, release_date.month, 1)

    rows: list[dict] = []
    warnings: list[str] = []
    for label, report, measure in _FLASH_BULLETS:
        # First decimal after the label is the current value ([^(]* skips both the
        # footnote "(n)" — consumed by _SEP — and any interleaved chart-axis ticks).
        m = re.search(label + _SEP + rf"[^(]*?{_DEC}", text, re.IGNORECASE)
        if m is None:
            warnings.append(f"flash bullet current value not found: {label!r}")
            continue
        rows.append(_row(report, measure, "flash", current, float(m.group(1)), release_date))
    return rows, current, warnings


def _parse_final(text: str, release_date: date) -> tuple[list[dict], date, list[str]]:
    # Best-effort: the final headline is prose with a direction-dependent verb.
    # If the current value can't be matched we return no rows (and a warning)
    # rather than raising, since the flash already supplies the predictively-
    # relevant services reading.
    m = _FINAL_CURRENT_RE.search(text)
    if m is None:
        return _parse_final_alt(text, release_date)
    current = _resolve_month(m.group(2), release_date)
    rows = [
        _row("services", "business_activity", "final", current, float(m.group(1)), release_date)
    ]

    warnings: list[str] = []
    # Prior value: behind a direction connector within a bounded window after the
    # current value; accept only if it resolves to the month just before current
    # (guards against grabbing a sub-index or year-ago figure mentioned nearby).
    pm = _FINAL_PRIOR_RE.search(text, m.end(), m.end() + 200)
    if pm is not None:
        prior = _resolve_month(pm.group(2), release_date)
        if prior == _prior_month(current):
            rows.append(
                _row(
                    "services",
                    "business_activity",
                    "final",
                    prior,
                    float(pm.group(1)),
                    release_date,
                )
            )
        else:
            warnings.append(
                f"final: candidate prior month {prior.isoformat()} is not one before "
                f"current {current.isoformat()}; prior value skipped"
            )
    return rows, current, warnings


def _parse_final_alt(text: str, release_date: date) -> tuple[list[dict], date, list[str]]:
    # Month-first fallback: "...Index fell in February, decreasing to 51.7 from 52.7".
    a = _FINAL_ALT_RE.search(text)
    if a is None:
        return (
            [],
            date(release_date.year, release_date.month, 1),
            ["final release: Services Business Activity Index value not matched; skipped"],
        )
    current = _resolve_month(a.group(1), release_date)
    prior = _prior_month(current)
    rows = [
        _row("services", "business_activity", "final", current, float(a.group(2)), release_date),
        _row("services", "business_activity", "final", prior, float(a.group(3)), release_date),
    ]
    return rows, current, []


def _row(
    report: str, measure: str, release_type: str, obs: date, value: float, release: date
) -> dict:
    return {
        "report": report,
        "measure": measure,
        "release_type": release_type,
        "observation_month": obs.isoformat(),
        "value": value,
        "units": "index",
        "release_date": release.isoformat(),
    }


def _find_release_date(text: str) -> date:
    for line in text.splitlines():
        if "embargoed" in line.lower():
            m = _EMBARGO_DATE_RE.search(line)
            if m:
                return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    # Fall back to the first day-month-year anywhere in the document.
    m = _EMBARGO_DATE_RE.search(text)
    if m is None:
        raise RuntimeError("could not find a release date (e.g. '5 May 2026') in the release")
    return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))


def _resolve_month(name: str, anchor: date) -> date:
    """Resolve a printed month name to a first-of-month date near ``anchor``.

    The release names months without a year (e.g. "...in April following ... in
    March"). The survey month is always shortly before the publication date, so
    we anchor on it and wrap the year back when a name resolves into the future
    (e.g. December named in a January release)."""
    month = _MONTHS[name.lower()]
    candidate = date(anchor.year, month, 1)
    if candidate > date(anchor.year, anchor.month, 1):
        candidate = date(anchor.year - 1, month, 1)
    return candidate


def _prior_month(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)
