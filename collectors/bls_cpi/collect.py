import logging
from datetime import date

from google.cloud import bigquery

from collectors.bls_cpi.series import AP_SERIES, CPI_SERIES, ApSeries, CpiSeries
from collectors.common import LoadSpec, Settings
from collectors.common.bls import fetch_series, join_footnotes, parse_value, pct_change
from collectors.common.secrets import get_secret

_log = logging.getLogger(__name__)

TABLE = "bls_cpi.cpi_series"
TABLE_AP = "bls_cpi.average_prices"
LOOKBACK_YEARS = 20
BLS_API_KEY_SECRET = "bls-api-key"

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("item_code", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("seasonally_adjusted", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("year", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("period", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("period_name", "STRING"),
    # index level (reference base varies by item)
    bigquery.SchemaField("value", "FLOAT64"),
    # API-supplied percent changes: 1m is the published m/m (taken from the SA
    # series), 12m is the published y/y (taken from the NSA series).
    bigquery.SchemaField("pct_change_1m", "FLOAT64"),
    bigquery.SchemaField("pct_change_3m", "FLOAT64"),
    bigquery.SchemaField("pct_change_12m", "FLOAT64"),
    bigquery.SchemaField("footnotes", "STRING"),
]

# Same shape as SCHEMA minus seasonally_adjusted (AP series are NSA-only) and
# with value holding the average price in dollars instead of an index level.
AP_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("item_code", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("year", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("period", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("period_name", "STRING"),
    # average price in dollars ($/dozen, $/kWh, ...)
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("pct_change_1m", "FLOAT64"),
    bigquery.SchemaField("pct_change_3m", "FLOAT64"),
    bigquery.SchemaField("pct_change_12m", "FLOAT64"),
    bigquery.SchemaField("footnotes", "STRING"),
]


def collect(settings: Settings) -> list[LoadSpec]:
    today = date.today()
    end_year = today.year
    start_year = end_year - LOOKBACK_YEARS

    cu_index = {s.series_id: s for s in CPI_SERIES}
    ap_index = {s.series_id: s for s in AP_SERIES}
    api_key = get_secret(settings.project_id, BLS_API_KEY_SECRET)

    by_series = fetch_series(
        [*cu_index, *ap_index],
        start_year,
        end_year,
        api_key=api_key,
        calculations=True,
    )

    cpi_rows: list[dict] = []
    ap_rows: list[dict] = []
    for series_id, points in by_series.items():
        if series_id in cu_index:
            cpi_rows.extend(_rows_for_series(cu_index[series_id], points))
        else:
            ap_rows.extend(_ap_rows_for_series(ap_index[series_id], points))

    _log.info(
        "CPI series fetched",
        extra={
            "extras": {
                "series": len(by_series),
                "cpi_rows": len(cpi_rows),
                "ap_rows": len(ap_rows),
            }
        },
    )
    return [
        LoadSpec(table=TABLE, schema=SCHEMA, rows=cpi_rows),
        LoadSpec(table=TABLE_AP, schema=AP_SCHEMA, rows=ap_rows),
    ]


def _rows_for_series(meta: CpiSeries, points: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for point in points:
        row = _base_row(meta, point)
        if row is not None:
            row["seasonally_adjusted"] = meta.seasonally_adjusted
            rows.append(row)
    return rows


def _ap_rows_for_series(meta: ApSeries, points: list[dict]) -> list[dict]:
    return [row for point in points if (row := _base_row(meta, point)) is not None]


def _base_row(meta: CpiSeries | ApSeries, point: dict) -> dict | None:
    obs_date = _period_to_date(point["year"], point["period"])
    if obs_date is None:
        return None  # skip annual averages (M13) and other non-monthly periods
    return {
        "series_id": meta.series_id,
        "item_code": meta.item_code,
        "description": meta.description,
        "observation_date": obs_date.isoformat(),
        "year": int(point["year"]),
        "period": point["period"],
        "period_name": point.get("periodName"),
        "value": parse_value(point.get("value")),
        "pct_change_1m": pct_change(point, "1"),
        "pct_change_3m": pct_change(point, "3"),
        "pct_change_12m": pct_change(point, "12"),
        "footnotes": join_footnotes(point.get("footnotes", [])),
    }


def _period_to_date(year: str, period: str) -> date | None:
    if period.startswith("M"):
        month = int(period[1:])
        if 1 <= month <= 12:
            return date(int(year), month, 1)
    return None
