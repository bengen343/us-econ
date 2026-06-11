"""BEA light-vehicle unit sales collector.

Lands monthly U.S. light vehicle unit sales -- autos and light trucks,
seasonally adjusted at annual rates, plus the NSA unit counts -- into
``bea_vehicles.unit_sales``, from the BEA API's underlying-detail table
``U70205S`` ("Auto and Truck Unit Sales, Production, Inventories,
Expenditures, and Price", monthly from 1967/1976):

    https://apps.bea.gov/api/data?method=GetData&DataSetName=NIUnderlyingDetail
        &TableName=U70205S&Frequency=M&Year=...

The table gets its preliminary month ~the 2nd business day of M+1 (the
"Supplemental Estimates: Motor Vehicles" update FRED's TOTALSA mirrors) and
revisions around day 20. Requires the free registered key (Secret Manager:
``bea-api-key``). The static Section7All workbook regenerates only with the
NIPA cycle and lags weeks -- it is NOT a substitute for the current month.

Vehicle sales are the month-M auto input of the retail-sales forecast
(forecasts/census_retail/headline_mm): motor vehicle & parts dealers are
~20% of the MARTS headline. The light-vehicle total the forecast uses is
SAART + TEMF + TEMG (autos SAAR, millions + light trucks <=14k lbs domestic
+ imported SAAR), computed downstream from the per-series rows landed here.

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


def call_with_error_check(call) -> dict:
    """BEA returns HTTP 200 with an error block on bad requests -- surface it."""
    body = with_retries(call)
    results = (body.get("BEAAPI") or {}).get("Results") or {}
    error = results.get("Error") or (body.get("BEAAPI") or {}).get("Error")
    if error:
        raise RuntimeError(f"BEA API error: {error}")
    return body


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
