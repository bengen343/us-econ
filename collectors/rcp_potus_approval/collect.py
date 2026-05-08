import html as html_lib
import json
import logging
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

URL = "https://www.realclearpolling.com/polls/approval/donald-trump/approval-rating"
TABLE = "rcp_potus_approval.polls"
POTUS = "trump"

# Realclearpolling rejects our default UA with 403; a stock browser UA passes.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Upgrade-Insecure-Requests": "1",
}

# The poll table is server-rendered into a Next.js streaming flight payload as
# an escaped JSON-in-JS-string fragment. We anchor on this marker.
POLLS_MARKER = r'\"polls\":['

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("potus", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("pollster", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("survey_start", "DATE"),
    bigquery.SchemaField("survey_end", "DATE"),
    bigquery.SchemaField("sample_size", "INT64"),
    bigquery.SchemaField("sample_type", "STRING"),
    bigquery.SchemaField("approve_pct", "FLOAT64"),
    bigquery.SchemaField("disapprove_pct", "FLOAT64"),
]


def collect(settings: Settings) -> LoadSpec:
    with client() as http:
        page = _fetch_page(http, URL)

    polls = _extract_polls(page)
    observation_date = datetime.now(ZoneInfo("America/Denver")).date()
    rows = _build_rows(polls, observation_date)
    _log.info(
        "RCP approval polls parsed",
        extra={
            "extras": {
                "row_count": len(rows),
                "polls_in_payload": len(polls),
                "observation_date": observation_date.isoformat(),
                "url": URL,
            }
        },
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _fetch_page(http: httpx.Client, url: str) -> str:
    def call() -> str:
        response = http.get(url, headers=BROWSER_HEADERS)
        response.raise_for_status()
        return response.text

    return with_retries(call)


def _extract_polls(page: str) -> list[dict[str, Any]]:
    """Locate the polls JSON array in the Next.js streaming payload and parse it."""
    marker = page.find(POLLS_MARKER)
    if marker == -1:
        raise RuntimeError("could not locate polls payload in RCP page")

    array_start = marker + len(POLLS_MARKER) - 1  # index of the leading `[`
    array_end = _find_balanced_array_end(page, array_start)
    if array_end is None:
        raise RuntimeError("polls array in RCP page was not bracket-balanced")

    # The slice is a fragment of a JS string literal: every `"` is `\"`,
    # every `\` is `\\`. Wrap as a JSON string and decode, then parse.
    slice_ = page[array_start:array_end]
    try:
        unescaped = json.loads('"' + slice_ + '"')
        return json.loads(unescaped)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse polls payload: {exc}") from exc


def _find_balanced_array_end(text: str, start: int) -> int | None:
    """Return the index just past the `]` that balances the `[` at ``start``,
    treating `\\"...\\"` regions as opaque (their brackets don't count)."""
    depth = 0
    in_str = False
    escape_next = False
    for j in range(start, len(text)):
        ch = text[j]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def _build_rows(polls: list[dict[str, Any]], observation_date: date) -> list[dict]:
    rows: list[dict] = []
    for poll in polls:
        if poll.get("type") == "rcp_average":
            continue

        pollster = html_lib.unescape((poll.get("pollster") or "").strip())
        if not pollster:
            _log.warning(
                "skipping poll with empty pollster",
                extra={"extras": {"poll_id": poll.get("id")}},
            )
            continue

        candidates = {c.get("name"): c.get("value") for c in poll.get("candidate") or []}
        sample_size, sample_type = _parse_sample(poll.get("sampleSize"))
        rows.append(
            {
                "observation_date": observation_date.isoformat(),
                "potus": POTUS,
                "pollster": pollster,
                "survey_start": _parse_iso_slash_date(poll.get("data_start_date")),
                "survey_end": _parse_iso_slash_date(poll.get("data_end_date")),
                "sample_size": sample_size,
                "sample_type": sample_type,
                "approve_pct": _parse_float(candidates.get("Approve")),
                "disapprove_pct": _parse_float(candidates.get("Disapprove")),
            }
        )
    return rows


_SAMPLE_RE = re.compile(r"^\s*(\d+)\s*([A-Za-z]+)?\s*$")


def _parse_sample(raw: str | None) -> tuple[int | None, str | None]:
    if not raw:
        return None, None
    match = _SAMPLE_RE.match(raw)
    if not match:
        return None, None
    size_str, type_str = match.groups()
    return int(size_str), (type_str.upper() if type_str else None)


def _parse_iso_slash_date(raw: str | None) -> str | None:
    """Parse RCP's `YYYY/MM/DD` survey dates into ISO `YYYY-MM-DD`."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y/%m/%d").date().isoformat()
    except ValueError:
        return None


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None
