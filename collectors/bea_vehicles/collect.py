"""BEA light-vehicle unit sales collector.

Lands monthly U.S. light vehicle unit sales -- autos and light trucks,
seasonally adjusted at annual rates, plus the NSA unit counts -- into
``bea_vehicles.unit_sales``, from the BEA API's underlying-detail table
``U70205S`` ("Auto and Truck Unit Sales, Production, Inventories,
Expenditures, and Price", monthly from 1967/1976):

    https://apps.bea.gov/api/data?method=GetData&DataSetName=NIUnderlyingDetail
        &TableName=U70205S&Frequency=M&Year=...

TIMING (verified 2026-06): the "Supplemental Estimates, Motor Vehicles"
update lands ~the 25th of M+1 -- the start-of-month "auto sales day" died
when the manufacturers moved to quarterly reporting; early-month SAARs in
the press are private estimators (Wards/NADA/S&P), not BEA. Month-M BEA
data is therefore NOT available before the mid-(M+1) MARTS release, which
is why the retail-sales forecast's winning spec carries no vehicle term
(forecasts/census_retail/headline_mm -- dveh_0 topped the raw leaderboard
but is not point-in-time legal). This collector lands the series as general
macro inputs (lag-1+ is PIT-clean for mid-month forecasts; the light-vehicle
total is SAART + TEMF + TEMG). Requires the free registered key (Secret
Manager: ``bea-api-key``).

EIA-style MERGE upsert on (series_code, observation_month): BEA restates the
recent months in place (preliminary -> ~day-20 revision -> NIPA benchmark),
and each run re-pulls the full history, so the table stays one row per
series-month at the latest value.
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
TABLE = "bea_vehicles.unit_sales"
TABLE_NAME = "U70205S"
START_YEAR = 1967
_YEARS_PER_CALL = 20  # keep responses comfortably under BEA's size limits

# Series codes to land (from table U70205S line items).
SERIES = {
    "SAART": ("auto sales, total", "millions of units (SAAR)"),
    "SAARD": ("auto sales, domestic", "millions of units (SAAR)"),
    "SAARF": ("auto sales, foreign", "millions of units (SAAR)"),
    "TEMF": ("light truck sales <=14k lbs, domestic", "millions of units (SAAR)"),
    "TEMG": ("light truck sales <=14k lbs, imports", "millions of units (SAAR)"),
    "NSAT": ("auto sales, total", "thousands of units (NSA)"),
}

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series_code", "STRING", mode="REQUIRED"),  # e.g. SAART
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]

MERGE_KEYS = ["series_code", "observation_month"]


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
                "DataSetName": "NIUnderlyingDetail",
                "TableName": TABLE_NAME,
                "Frequency": "M",
                "Year": years,
                "ResultFormat": "JSON",
            }

            def call(params: dict = params) -> dict:
                response = http.get(BEA_API_URL, params=params)
                response.raise_for_status()
                return response.json()

            body = call_with_error_check(call)
            window_rows = _parse(body)
            rows.extend(window_rows)
            _log.info(
                "BEA window fetched",
                extra={"extras": {"years": years, "rows": len(window_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=_dedupe(rows), merge_keys=MERGE_KEYS)


def call_with_error_check(call, attempts: int = 3) -> dict:
    """BEA returns HTTP 200 with an error block on bad requests, so
    with_retries can't see failures -- surface them, retrying a couple of
    times first (BEA's load-balanced nodes are occasionally inconsistent;
    notably, a freshly activated key propagates node-by-node, surfacing as
    intermittent APIErrorCode 4 'UserId is not active')."""
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
        code = point.get("SeriesCode")
        if code not in SERIES:
            continue
        period = point.get("TimePeriod", "")  # e.g. 2026M04
        if "M" not in period:
            continue
        year, month = period.split("M")
        raw = (point.get("DataValue") or "").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            value = None
        description, units = SERIES[code]
        rows.append(
            {
                "series_code": code,
                "description": description,
                "observation_month": f"{int(year):04d}-{int(month):02d}-01",
                "value": value,
                "units": units,
            }
        )
    return rows


def _dedupe(rows: list[dict]) -> list[dict]:
    """Collapse duplicate merge-key rows (overlapping windows / API repeats),
    last occurrence wins -- two source rows per key break the MERGE."""
    by_key: dict[tuple, dict] = {}
    for row in rows:
        by_key[tuple(row[key] for key in MERGE_KEYS)] = row
    dropped = len(rows) - len(by_key)
    if dropped:
        _log.info("dropped duplicate merge-key rows", extra={"extras": {"dropped": dropped}})
    return list(by_key.values())
