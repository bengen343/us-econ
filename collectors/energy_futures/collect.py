"""Energy futures collector (Yahoo Finance).

Lands the low-latency daily market signals the AAA gasoline next-day forecast
leans on: front-month RBOB gasoline (RB=F), WTI crude (CL=F), and Brent crude
(BZ=F) futures, from the Yahoo Finance v8 chart API.

Why futures here rather than EIA: RB=F is the RBOB benchmark that retail pump
prices track with a lag, and EIA has no usable daily RBOB series -- its daily
NY-Harbor RBOB spot is unpopulated, and its monthly refiner wholesale/resale
gasoline price program was discontinued in 2022-03. CL=F/BZ=F complement EIA's
daily WTI/Brent *spot* (RWTC/RBRTE in eia_petroleum.prices) with same-day-
settling *futures* that carry forward-looking information.

Yahoo is best-effort: occasional gaps, and the in-progress trading day is
returned with a provisional (non-final) close. Like eia_petroleum this re-pulls
full history on every run and UPSERTs on (ticker, observation_date), so a
provisional close is overwritten by the settled value on the next run and the
table stays one-row-per-ticker-date.
"""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
TABLE = "energy_futures.daily"
# 2000-01-01 UTC. The full daily history is requested with explicit period
# bounds: range="max" silently downsamples long spans to monthly bars, whereas
# period1/period2 + interval=1d returns true daily data.
START_EPOCH = 946684800
# Yahoo serves the chart endpoint to browser-like clients; the default collector
# User-Agent gets rejected, so send a browser UA on these requests.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# ticker -> stable instrument label. Yahoo's `shortName` embeds the rolling
# contract month (e.g. "RBOB Gasoline Aug 26"), so we label by ticker instead to
# keep the column stable as the front month rolls.
TICKERS: dict[str, str] = {
    "RB=F": "RBOB Gasoline (front-month future)",
    "CL=F": "WTI Crude Oil (front-month future)",
    "BZ=F": "Brent Crude Oil (front-month future)",
}

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("ticker", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("currency", "STRING"),
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("open", "FLOAT64"),
    bigquery.SchemaField("high", "FLOAT64"),
    bigquery.SchemaField("low", "FLOAT64"),
    bigquery.SchemaField("close", "FLOAT64"),
    bigquery.SchemaField("volume", "INT64"),
]

MERGE_KEYS = ["ticker", "observation_date"]


def collect(settings: Settings) -> LoadSpec:
    rows: list[dict] = []
    with client() as http:
        for ticker, name in TICKERS.items():
            result = _fetch(http, ticker)
            ticker_rows = _rows(result, ticker, name)
            rows.extend(ticker_rows)
            _log.info(
                "Yahoo futures fetched",
                extra={"extras": {"ticker": ticker, "rows": len(ticker_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows, merge_keys=MERGE_KEYS)


def _fetch(http, ticker: str) -> dict:
    """Pull the full daily history for one ticker; returns the chart result dict."""
    now_epoch = int(datetime.now(UTC).timestamp()) + 86400  # +1d so today is inclusive
    params = {"period1": START_EPOCH, "period2": now_epoch, "interval": "1d"}

    def call() -> dict:
        response = http.get(
            CHART_URL.format(ticker=ticker),
            params=params,
            headers={"User-Agent": BROWSER_UA},
        )
        response.raise_for_status()
        return response.json()

    body = with_retries(call)
    chart = body.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart error for {ticker}: {chart['error']}")
    result = chart.get("result")
    if not result:
        raise RuntimeError(f"Yahoo chart returned no result for {ticker}")
    return result[0]


def _rows(result: dict, ticker: str, name: str) -> list[dict]:
    meta = result.get("meta") or {}
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    currency = meta.get("currency")

    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    # Key by trading date (timestamps are at exchange open; convert in the
    # exchange tz to avoid an off-by-one UTC date). Yahoo can repeat the current
    # day -- once as the regular session bar and once as a live quote -- so a
    # dict de-dupes it, last write wins.
    by_date: dict[str, dict] = {}
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue  # skip the provisional in-progress bar that has no close yet
        obs_date = datetime.fromtimestamp(ts, tz).date().isoformat()
        volume = volumes[i] if i < len(volumes) else None
        by_date[obs_date] = {
            "ticker": ticker,
            "name": name,
            "currency": currency,
            "observation_date": obs_date,
            "open": _parse_float(opens[i] if i < len(opens) else None),
            "high": _parse_float(highs[i] if i < len(highs) else None),
            "low": _parse_float(lows[i] if i < len(lows) else None),
            "close": _parse_float(close),
            "volume": int(volume) if volume is not None else None,
        }
    return list(by_date.values())


def _parse_float(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
