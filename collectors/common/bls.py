"""Shared BLS Public Data API v2 client.

Centralises the quirks of the BLS timeseries API so individual collectors
(``bls_cpi``, ``bls_ppi``, ...) only declare their series and map rows:

  * **Series batching.** A single request is capped at 50 series with a
    registration key (25 without). Series IDs are batched under the limit.
  * **Year-span clamping.** A single request is also capped at 20 years with a
    key (10 without). Critically, when the requested range exceeds the cap the
    API does NOT error -- it returns REQUEST_SUCCEEDED and silently clamps the
    range *from startyear forward*, dropping the most recent year(s). We request
    in windows no wider than the cap so the current year is never dropped, then
    merge points back together per series.
  * **Calculations.** With ``calculations=True`` the API attaches a
    ``calculations.pct_changes`` block ({"1","3","12"} -> percent change over
    that many months) to each data point -- the published m/m / 3-month / y/y
    changes. These are continuous within a contiguous request range but null for
    the first 12 months of the earliest window, so callers that need gap-free
    changes should derive them from the index level instead.

``bls_employment`` predates this helper and keeps its own inline copy; it is
intentionally left untouched.
"""

from __future__ import annotations

from collectors.common.http import client, with_retries

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Per-request caps: (with key, without key).
_MAX_SERIES_WITH_KEY = 50
_MAX_SERIES_WITHOUT_KEY = 25
_MAX_YEARS_WITH_KEY = 20
_MAX_YEARS_WITHOUT_KEY = 10


def fetch_series(
    series_ids: list[str],
    start_year: int,
    end_year: int,
    *,
    api_key: str | None,
    calculations: bool = False,
) -> dict[str, list[dict]]:
    """Fetch BLS timeseries, returning ``series_id -> list of raw point dicts``.

    Points are merged across the year windows and series batches required to
    stay under the API caps, deduplicated by (year, period) keeping the first
    occurrence. Raises on any non-success API status.
    """
    batch_size = _MAX_SERIES_WITH_KEY if api_key else _MAX_SERIES_WITHOUT_KEY
    max_years = _MAX_YEARS_WITH_KEY if api_key else _MAX_YEARS_WITHOUT_KEY
    windows = _year_windows(start_year, end_year, max_years)

    merged: dict[str, dict[tuple[str, str], dict]] = {sid: {} for sid in series_ids}
    with client() as http:
        for start in range(0, len(series_ids), batch_size):
            batch = series_ids[start : start + batch_size]
            for win_start, win_end in windows:
                payload: dict = {
                    "seriesid": batch,
                    "startyear": str(win_start),
                    "endyear": str(win_end),
                }
                if calculations:
                    payload["calculations"] = True
                if api_key:
                    payload["registrationkey"] = api_key

                def call(payload: dict = payload) -> dict:
                    response = http.post(BLS_API_URL, json=payload)
                    response.raise_for_status()
                    return response.json()

                body = with_retries(call)
                if body.get("status") != "REQUEST_SUCCEEDED":
                    raise RuntimeError(
                        f"BLS API failure: status={body.get('status')!r} "
                        f"message={body.get('message')!r}"
                    )

                for series in body["Results"]["series"]:
                    sid = series["seriesID"]
                    bucket = merged.setdefault(sid, {})
                    for point in series.get("data", []):
                        bucket.setdefault((point["year"], point["period"]), point)

    return {sid: list(points.values()) for sid, points in merged.items()}


def pct_change(point: dict, months: str) -> float | None:
    """Extract a percent change ("1"/"3"/"12") from a point's calculations block."""
    raw = (point.get("calculations") or {}).get("pct_changes", {}).get(months)
    return _parse_float(raw)


def parse_value(raw: str | None) -> float | None:
    """Parse a BLS value string, mapping the sentinels '', '-' to None."""
    if raw is None or raw in ("", "-"):
        return None
    return _parse_float(raw)


def join_footnotes(footnotes: list[dict]) -> str | None:
    parts = [fn.get("text") or fn.get("code") for fn in footnotes if fn]
    parts = [p for p in parts if p]
    return "; ".join(parts) if parts else None


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _year_windows(start_year: int, end_year: int, max_span: int) -> list[tuple[int, int]]:
    """Split [start_year, end_year] into inclusive windows no wider than max_span."""
    if max_span < 1:
        raise ValueError(f"max_span must be >= 1, got {max_span}")
    windows: list[tuple[int, int]] = []
    win_start = start_year
    while win_start <= end_year:
        win_end = min(win_start + max_span - 1, end_year)
        windows.append((win_start, win_end))
        win_start = win_end + 1
    return windows
