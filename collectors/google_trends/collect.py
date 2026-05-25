import logging
import math
import time
from datetime import date, timedelta
from typing import Any

import requests.exceptions
from google.cloud import bigquery
from pytrends.exceptions import ResponseError, TooManyRequestsError
from pytrends.request import TrendReq

from collectors.common import LoadSpec, Settings
from collectors.google_trends.series import GEO, LOOKBACK_TIMEFRAME, TERMS, TrendsTerm

_log = logging.getLogger(__name__)

TABLE = "google_trends.weekly"
SOURCE = "pytrends"

# pytrends is brittle and Trends rate-limits aggressively. We do one request
# per term (own scale, no cross-term renormalization), with pacing between
# terms and exponential backoff on errors.
PER_TERM_DELAY_SECS = 2.0
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECS = 5.0  # 5, 10, 20, 40, 80 seconds

# Errors worth retrying on -- transient HTTP / quota issues.
_RETRYABLE = (
    TooManyRequestsError,
    ResponseError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("kind", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("term", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("geo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("week_ending", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("vintage_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("is_partial", "BOOL"),
]


def collect(settings: Settings) -> LoadSpec:
    vintage = date.today()
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 30), retries=0, backoff_factor=0)

    rows: list[dict] = []
    for term in TERMS:
        df = _fetch_term(pytrends, term)
        if df is None or df.empty:
            _log.warning(
                "no data returned for term",
                extra={"extras": {"series_id": term.series_id, "kind": term.kind}},
            )
            continue
        rows.extend(_build_rows(df, term, vintage))
        time.sleep(PER_TERM_DELAY_SECS)

    _log.info(
        "google trends collected",
        extra={
            "extras": {
                "row_count": len(rows),
                "terms_fetched": len(TERMS),
                "vintage_date": vintage.isoformat(),
            }
        },
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _fetch_term(pytrends: TrendReq, term: TrendsTerm):
    """Run one pytrends call for `term`, retrying transient errors with backoff."""
    if term.kind == "category":
        # category-only: empty keyword + cat= filter. The returned DataFrame
        # column name will be the empty string; _build_rows handles it.
        kw_list = [""]
        cat = int(term.term)
    elif term.kind in ("topic", "query"):
        # topic MIDs go in kw_list just like a query string; pytrends resolves
        # them server-side.
        kw_list = [term.term]
        cat = 0
    else:
        raise ValueError(f"unknown term kind: {term.kind!r}")

    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            pytrends.build_payload(
                kw_list=kw_list, cat=cat, timeframe=LOOKBACK_TIMEFRAME, geo=GEO,
            )
            return pytrends.interest_over_time()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt == MAX_ATTEMPTS - 1:
                _log.exception(
                    "pytrends fetch failed after retries",
                    extra={"extras": {"series_id": term.series_id, "attempts": attempt + 1}},
                )
                raise
            sleep_for = BASE_BACKOFF_SECS * (2**attempt)
            _log.warning(
                "pytrends fetch transient error; backing off",
                extra={
                    "extras": {
                        "series_id": term.series_id,
                        "attempt": attempt + 1,
                        "sleep_secs": sleep_for,
                        "error": repr(exc),
                    }
                },
            )
            time.sleep(sleep_for)
    raise RuntimeError("unreachable") from last_exc


def _build_rows(df, term: TrendsTerm, vintage: date) -> list[dict]:
    """pytrends DataFrame -> row dicts. Trends weeks are Sunday-start; we shift
    +6 days so week_ending is Saturday, aligning directly with claims weeks."""
    value_col = _value_column(df)
    partial_col = "isPartial" if "isPartial" in df.columns else None

    rows: list[dict] = []
    for ts, row in df.iterrows():
        week_start = ts.to_pydatetime().date() if hasattr(ts, "to_pydatetime") else None
        if week_start is None:
            continue
        week_ending = week_start + timedelta(days=6)
        rows.append(
            {
                "series_id": term.series_id,
                "source": SOURCE,
                "kind": term.kind,
                "term": term.term,
                "geo": GEO,
                "description": term.description,
                "week_ending": week_ending.isoformat(),
                "vintage_date": vintage.isoformat(),
                "value": _coerce_float(row[value_col]),
                "is_partial": _coerce_bool(row[partial_col]) if partial_col else False,
            }
        )
    return rows


def _value_column(df) -> str:
    """First non-isPartial column -- handles both query/topic (named after the
    term string) and category (named ''/empty string)."""
    for col in df.columns:
        if col != "isPartial":
            return col
    raise RuntimeError("pytrends DataFrame has no value column")


def _coerce_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _coerce_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.strip().lower() in ("true", "1", "yes")
    if x is None:
        return False
    try:
        return bool(int(x))
    except (TypeError, ValueError):
        return bool(x)
