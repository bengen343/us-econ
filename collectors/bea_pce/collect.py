"""BEA PCE price index collector.

Lands the monthly PCE price indexes -- every line of NIPA table T20804
("Price Indexes for Personal Consumption Expenditures by Major Type of
Product", 1959+), including the headline (DPCERG), core (DPCCRG), and
market-based variants -- into ``bea_pce.price_indexes``, from the BEA API
(key in Secret Manager: ``bea-api-key``).

The release calendar: PCE for month M arrives with Personal Income and
Outlays ~the 25th-31st of M+1 (08:30 ET). Core PCE m/m (DPCCRG) is the
target of forecasts/bea_pce/core_mm.

Append-only and vintage-stamped (like the BLS collectors): PCE is revised
at each subsequent release and re-benchmarked annually, so each run
re-appends the full table and consumers dedupe to the latest vintage per
(series_code, month) via ``ingested_at``; first prints accrue for revision
studies.

BEA API quirks (learned in collectors/bea_vehicles): errors come back as
HTTP 200 bodies (retried here), and a freshly activated key propagates
node-by-node for ~30+ minutes, surfacing as intermittent APIErrorCode 4.
"""

import logging
from datetime import date

from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries
from collectors.common.secrets import get_secret

_log = logging.getLogger(__name__)

BEA_API_URL = "https://apps.bea.gov/api/data"
BEA_API_KEY_SECRET = "bea-api-key"
TABLE = "bea_pce.price_indexes"
TABLE_NAME = "T20804"
START_YEAR = 1959
_YEARS_PER_CALL = 20

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series_code", "STRING", mode="REQUIRED"),  # e.g. DPCCRG
    bigquery.SchemaField("line_description", "STRING"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]
UNITS = "index (2017=100, SA)"


def collect(settings: Settings) -> LoadSpec:
    api_key = get_secret(settings.project_id, BEA_API_KEY_SECRET)
    if not api_key:
        raise RuntimeError(f"BEA API key not found in Secret Manager: {BEA_API_KEY_SECRET}")

    rows: list[dict] = []
    current_year = date.today().year
    with client(timeout=120.0) as http:
        for start in range(START_YEAR, current_year + 1, _YEARS_PER_CALL):
            years = ",".join(
                str(y) for y in range(start, min(start + _YEARS_PER_CALL, current_year + 1))
            )
            params = {
                "UserID": api_key,
                "method": "GetData",
                "DataSetName": "NIPA",
                "TableName": TABLE_NAME,
                "Frequency": "M",
                "Year": years,
                "ResultFormat": "JSON",
            }

            def call(params: dict = params) -> dict:
                response = http.get(BEA_API_URL, params=params)
                response.raise_for_status()
                return response.json()

            body = _call_with_error_check(call)
            window_rows = _parse(body)
            rows.extend(window_rows)
            _log.info(
                "BEA window fetched",
                extra={"extras": {"years": years, "rows": len(window_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=_dedupe_keep_last(rows))


def _call_with_error_check(call, attempts: int = 3) -> dict:
    """BEA returns HTTP 200 with an error block -- surface it, with retries
    (load-balanced nodes are occasionally inconsistent)."""
    import time

    error = None
    for attempt in range(attempts):
        body = with_retries(call)
        results = (body.get("BEAAPI") or {}).get("Results") or {}
        error = results.get("Error") or (body.get("BEAAPI") or {}).get("Error")
        if not error:
            return body
        if attempt < attempts - 1:
            _log.warning("BEA API error body; retrying", extra={"extras": {"error": str(error)}})
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"BEA API error: {error}")


def _parse(body: dict) -> list[dict]:
    data = ((body.get("BEAAPI") or {}).get("Results") or {}).get("Data") or []
    rows: list[dict] = []
    for point in data:
        period = point.get("TimePeriod", "")
        if "M" not in period:
            continue
        year, month = period.split("M")
        raw = (point.get("DataValue") or "").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            value = None
        rows.append(
            {
                "series_code": point.get("SeriesCode"),
                "line_description": point.get("LineDescription"),
                "observation_month": f"{int(year):04d}-{int(month):02d}-01",
                "value": value,
                "units": UNITS,
            }
        )
    return rows


def _dedupe_keep_last(rows: list[dict]) -> list[dict]:
    """The API repeats series that appear on multiple table lines (e.g.
    DPCERG); collapse to one row per (series_code, month) within this run --
    the table itself stays append-only across runs."""
    by_key: dict[tuple, dict] = {}
    for row in rows:
        by_key[(row["series_code"], row["observation_month"])] = row
    return list(by_key.values())
