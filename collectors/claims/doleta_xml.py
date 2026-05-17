import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import NamedTuple
from urllib.parse import urlencode

import httpx

from collectors.claims.series import MEASURES_BY_KEY, STATE_CODES, STATE_NAME_TO_CODE
from collectors.common.http import with_retries

REPORT_URL = "https://oui.doleta.gov/unemploy/wkclaims/report.asp"
SOURCE = "doleta_xml"
EMPTY_CELL = "\xa0"  # non-breaking space; doleta uses this for unreleased weeks

_log = logging.getLogger(__name__)


class FetchResult(NamedTuple):
    rundate: date
    rows: list[dict]


def fetch_all(
    http: httpx.Client, *, start_year: int, end_year: int
) -> FetchResult:
    """Fetch national + all-state weekly claims XML and return merged rows."""
    national = _fetch_national(http, start_year=start_year, end_year=end_year)
    state = _fetch_states(http, start_year=start_year, end_year=end_year)
    if state.rundate != national.rundate:
        _log.warning(
            "doleta national/state rundate mismatch",
            extra={
                "extras": {
                    "national_rundate": national.rundate.isoformat(),
                    "state_rundate": state.rundate.isoformat(),
                }
            },
        )
    return FetchResult(rundate=national.rundate, rows=national.rows + state.rows)


def _fetch_national(http: httpx.Client, *, start_year: int, end_year: int) -> FetchResult:
    payload = [
        ("level", "us"),
        ("final_yr", str(end_year + 1)),
        ("strtdate", str(start_year)),
        ("enddate", str(end_year)),
        ("filetype", "xml"),
        ("submit", "Submit"),
    ]
    root = _post_xml(http, payload)
    rundate = _parse_rundate(root.get("rundate"))
    rows: list[dict] = []
    for week in root.findall("week"):
        we = _parse_us_date(week.findtext("weekEnded"))
        if we is None:
            continue
        for seg_tag, measure in (
            ("InitialClaims", "initial_claims"),
            ("ContinuedClaims", "continued_claims"),
        ):
            seg = week.find(seg_tag)
            if seg is None:
                continue
            # SF (seasonal factor) is published ahead of the data, so future
            # weeks carry an SF even though NSA/SA/SA4WK are still blank. Keeping
            # it here is what retains those forward-dated rows for forecasting.
            for sa_tag, sa_key in (
                ("NSA", "nsa"),
                ("SA", "sa"),
                ("SA4WK", "sa4wk_ma"),
                ("SF", "sf"),
            ):
                row = _make_row(
                    level="national",
                    area="US",
                    measure=measure,
                    sa=sa_key,
                    week_ending=we,
                    vintage_date=rundate,
                    raw_value=seg.findtext(sa_tag),
                )
                if row is not None:
                    rows.append(row)

        iur = week.find("IUR")
        if iur is not None:
            for sa_tag, sa_key in (("NSA", "nsa"), ("SA", "sa")):
                row = _make_row(
                    level="national",
                    area="US",
                    measure="iur",
                    sa=sa_key,
                    week_ending=we,
                    vintage_date=rundate,
                    raw_value=iur.findtext(sa_tag),
                )
                if row is not None:
                    rows.append(row)

        row = _make_row(
            level="national",
            area="US",
            measure="covered_employment",
            sa="nsa",
            week_ending=we,
            vintage_date=rundate,
            raw_value=week.findtext("CoveredEmployment"),
        )
        if row is not None:
            rows.append(row)
    return FetchResult(rundate=rundate, rows=rows)


def _fetch_states(http: httpx.Client, *, start_year: int, end_year: int) -> FetchResult:
    payload: list[tuple[str, str]] = [
        ("level", "state"),
        ("final_yr", str(end_year + 1)),
        ("strtdate", str(start_year)),
        ("enddate", str(end_year)),
        ("filetype", "xml"),
        ("submit", "Submit"),
    ]
    payload.extend(("states[]", code) for code in STATE_CODES)

    root = _post_xml(http, payload)
    rundate = _parse_rundate(root.get("rundate"))
    rows: list[dict] = []
    unmapped_states: set[str] = set()

    for week in root.findall("week"):
        name = (week.findtext("stateName") or "").strip()
        code = STATE_NAME_TO_CODE.get(name)
        if code is None:
            if name:
                unmapped_states.add(name)
            continue

        # State IC lags national by one week; ReflectingWeekEnded is the actual
        # data week. Fall back to weekEnded if missing for any reason.
        we = _parse_us_date(week.findtext("ReflectingWeekEnded")) or _parse_us_date(
            week.findtext("weekEnded")
        )
        if we is None:
            continue

        for tag, measure in (
            ("InitialClaims", "initial_claims"),
            ("ContinuedClaims", "continued_claims"),
            ("CoveredEmployment", "covered_employment"),
            ("InsuredUnemploymentRate", "iur"),
        ):
            row = _make_row(
                level="state",
                area=code,
                measure=measure,
                sa="nsa",
                week_ending=we,
                vintage_date=rundate,
                raw_value=week.findtext(tag),
            )
            if row is not None:
                rows.append(row)

    if unmapped_states:
        _log.warning(
            "doleta state names not in code map (skipped)",
            extra={"extras": {"states": sorted(unmapped_states)}},
        )

    return FetchResult(rundate=rundate, rows=rows)


def _post_xml(http: httpx.Client, payload: list[tuple[str, str]]) -> ET.Element:
    body = urlencode(payload)

    def call() -> ET.Element:
        response = http.post(
            REPORT_URL,
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/xml,application/xml",
            },
        )
        response.raise_for_status()
        return ET.fromstring(response.text)

    return with_retries(call)


def _make_row(
    *,
    level: str,
    area: str,
    measure: str,
    sa: str,
    week_ending: date,
    vintage_date: date,
    raw_value: str | None,
) -> dict | None:
    value = _parse_value(raw_value)
    if value is None:
        return None
    meta = MEASURES_BY_KEY[measure]
    if sa == "sf":
        description = f"Multiplicative seasonal factor for {measure.replace('_', ' ')}"
        units = "factor (base 100)"
    else:
        description = meta.description
        units = meta.units
    return {
        "series_id": f"doleta.{area.lower()}.{measure}.{sa}",
        "source": SOURCE,
        "level": level,
        "area": area,
        "measure": measure,
        "seasonal_adjustment": sa,
        "description": description,
        "units": units,
        "week_ending": week_ending.isoformat(),
        "vintage_date": vintage_date.isoformat(),
        "value": value,
    }


def _parse_value(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(EMPTY_CELL, "").replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_us_date(raw: str | None) -> date | None:
    if not raw:
        return None
    cleaned = raw.replace(EMPTY_CELL, "").strip()
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%m/%d/%Y").date()
    except ValueError:
        return None


def _parse_rundate(raw: str | None) -> date:
    parsed = _parse_us_date(raw)
    if parsed is None:
        raise RuntimeError(f"doleta XML missing or unparseable rundate attribute: {raw!r}")
    return parsed
