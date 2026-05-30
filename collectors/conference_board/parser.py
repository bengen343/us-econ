"""Parse The Conference Board Consumer Confidence press release.

The monthly release lives (latest only) at a stable landing URL and states its
figures in prose, not tables. The phrasing is highly templated and, crucially,
self-checking: it prints both the labor-market shares AND their differential
(and both business-condition shares AND their net), so a parse can validate
itself. We extract a broad set of series because the landing page only ever
shows the most recent month — history vanishes, so we capture what we can each
run (and the raw HTML is archived upstream as the catch-all).

Two values are captured per share/index measure where stated:
  * the current reference month's value, and
  * the immediately-prior month's value (from the "... from Y% [in Month]"
    clause). The release always compares to the prior calendar month, so the
    prior value is tagged to release_month - 1. This also makes the collector
    resilient to a missed run: next month's release restates this month.

Parsing is fail-soft per measure: a measure that isn't found is simply omitted
(NULL downstream), never an error — except that if none of the core anchors are
found we raise, because that means the page structure actually changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


# --------------------------------------------------------------------------- #
# Measures. Each pattern captures the CURRENT value in group 1. `units`:
# "index" (1985=100 index points), "percent" (survey shares), "ppts" (signed
# differentials). Patterns run case-insensitively on punctuation-normalised text.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Measure:
    key: str
    units: str
    pattern: str
    prior: bool = True  # try to capture the prior-month value from a "from Y%" clause


MEASURES: list[Measure] = [
    # Headline indices (1985=100). Confidence is uniquely anchored by "(1985=100)".
    Measure("confidence_index", "index", r"\bto\s+(\d{2,3}\.\d)\s*\(1985\s*=\s*100\)"),
    Measure(
        "present_situation_index", "index", r"Present Situation Index.{0,120}?\bto\s+(\d{2,3}\.\d)"
    ),
    Measure("expectations_index", "index", r"Expectations Index.{0,120}?\bto\s+(\d{2,3}\.\d)"),
    # Present-situation shares.
    Measure(
        "jobs_plentiful",
        "percent",
        r"(\d{1,2}\.\d)%\s+of consumers said jobs were\s+['\"]?plentiful",
    ),
    Measure(
        "jobs_hard_to_get",
        "percent",
        r"(\d{1,2}\.\d)%\s+(?:of consumers\s+)?said jobs were\s+['\"]?hard to get",
    ),
    Measure(
        "business_good",
        "percent",
        r"(\d{1,2}\.\d)%\s+of consumers said business conditions were\s+['\"]?good",
    ),
    Measure(
        "business_bad",
        "percent",
        r"(\d{1,2}\.\d)%\s+(?:of consumers\s+)?said business conditions were\s+['\"]?bad",
    ),
    # Six-months-ahead expectations shares.
    Measure(
        "exp_business_better",
        "percent",
        r"(\d{1,2}\.\d)%\s+of consumers expected business conditions to improve",
    ),
    Measure(
        "exp_business_worse", "percent", r"(\d{1,2}\.\d)%\s+expected business conditions to worsen"
    ),
    Measure("exp_jobs_more", "percent", r"(\d{1,2}\.\d)%\s+of consumers expected more jobs"),
    Measure("exp_jobs_fewer", "percent", r"(\d{1,2}\.\d)%\s+anticipated fewer jobs"),
    Measure(
        "exp_income_increase",
        "percent",
        r"(\d{1,2}\.\d)%\s+of consumers expected their incomes to increase",
    ),
    Measure("exp_income_decline", "percent", r"(\d{1,2}\.\d)%\s+expected their incomes to decline"),
    # Stated nets/differentials (signed ppts). No "from Y%" clause -> prior off.
    Measure(
        "labor_differential",
        "ppts",
        r"labor market differential.{0,220}?\bto\s+([+\-]?\d{1,2}\.\d)\s*%",
        prior=False,
    ),
    Measure(
        "business_net",
        "ppts",
        r"net views of current business conditions.{0,200}?\bto\s+([+\-]?\d{1,2}\.\d)\s*%",
        prior=False,
    ),
]

# Measures that, if ALL missing, mean the page structure broke (hard fail).
_CORE_KEYS = ("confidence_index", "jobs_plentiful", "jobs_hard_to_get")

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

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MONTHS_ALT = "|".join(_MONTHS)
_DATE_RE = re.compile(rf"\b({_MONTHS_ALT})\s+(\d{{1,2}}),\s+(20\d\d)\b", re.IGNORECASE)
# Authoritative publication date, e.g. "Latest Press Release Updated: Tuesday,
# May 26, 2026" — its month/year IS the reference month (released last Tuesday of
# the survey month). The page also lists many unrelated event dates, so we must
# anchor rather than grab the first date on the page.
_UPDATED_RE = re.compile(
    rf"Latest Press Release\s+Updated:\s*(?:[A-Za-z]+,\s*)?({_MONTHS_ALT})\s+\d{{1,2}},\s+(20\d\d)",
    re.IGNORECASE,
)
# Fallback: the reference month named in the headline index sentence
# ("... (1985=100) in May, ...").
_REF_MONTH_RE = re.compile(rf"\(1985\s*=\s*100\)\s+in\s+({_MONTHS_ALT})\b", re.IGNORECASE)
_PRIOR_RE = re.compile(r"from\s+(?:an?\s+\w+\s+revised\s+)?([+\-]?\d{1,3}\.\d)", re.IGNORECASE)


@dataclass
class ParseResult:
    release_month: date
    rows: list[dict]
    warnings: list[str] = field(default_factory=list)


def strip_html(html: str) -> str:
    """HTML -> punctuation-normalised plain text suitable for the templated regexes."""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    # Decode the handful of entities that appear in the release body.
    for entity, repl in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&rsquo;", "'"),
        ("&lsquo;", "'"),
        ("&ldquo;", '"'),
        ("&rdquo;", '"'),
        ("&mdash;", "-"),
        ("&ndash;", "-"),
        ("&#8217;", "'"),
        ("&#8220;", '"'),
        ("&#8221;", '"'),
        ("&#8212;", "-"),
    ):
        text = text.replace(entity, repl)
    # Normalise smart punctuation to ASCII so the patterns are quote/dash agnostic.
    text = (
        text.replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", " ")
    )
    return _WS_RE.sub(" ", text).strip()


def _prior_month(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def _detect_release_month(text: str) -> date:
    """Reference month = month of the 'Latest Press Release Updated:' date.

    Falls back to the headline index sentence's named month paired with the year
    of a full date that shares that month (avoids the page's unrelated event
    dates). Raises if neither anchor is found.
    """
    m = _UPDATED_RE.search(text)
    if m is not None:
        return date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
    rm = _REF_MONTH_RE.search(text)
    if rm is not None:
        month = _MONTHS[rm.group(1).lower()]
        for dm in _DATE_RE.finditer(text):
            if _MONTHS[dm.group(1).lower()] == month:
                return date(int(dm.group(3)), month, 1)
    raise RuntimeError("could not determine the release reference month")


def parse_release(html: str) -> ParseResult:
    text = strip_html(html)
    release_month = _detect_release_month(text)
    prior_month = _prior_month(release_month)

    rows: list[dict] = []
    current: dict[str, float] = {}
    for m in MEASURES:
        match = re.search(m.pattern, text, re.IGNORECASE)
        if match is None:
            continue
        cur = float(match.group(1))
        current[m.key] = cur
        rows.append(_row(m, release_month, release_month, cur))
        if m.prior:
            tail = text[match.end() : match.end() + 90]
            pm = _PRIOR_RE.search(tail)
            if pm is not None:
                rows.append(_row(m, prior_month, release_month, float(pm.group(1))))

    if not any(k in current for k in _CORE_KEYS):
        raise RuntimeError("no core measures parsed; Consumer Confidence page structure changed")

    return ParseResult(release_month, rows, _validate(current))


def _row(m: Measure, observation_month: date, release_month: date, value: float) -> dict:
    return {
        "measure": m.key,
        "observation_month": observation_month.isoformat(),
        "value": value,
        "units": m.units,
        "release_month": release_month.isoformat(),
    }


def _validate(cur: dict[str, float]) -> list[str]:
    """Self-consistency checks the release affords for free (within rounding)."""
    warnings: list[str] = []
    checks = [
        ("labor_differential", "jobs_plentiful", "jobs_hard_to_get"),
        ("business_net", "business_good", "business_bad"),
    ]
    for net, pos, neg in checks:
        if all(k in cur for k in (net, pos, neg)):
            implied = cur[pos] - cur[neg]
            if abs(implied - cur[net]) > 0.15:
                warnings.append(
                    f"{net} {cur[net]:+.1f} != {pos}-{neg} = {implied:+.1f} (parse drift?)"
                )
    return warnings
