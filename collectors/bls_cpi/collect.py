import logging
from datetime import date

from google.cloud import bigquery

from collectors.bls_cpi.series import CPI_SERIES, CpiSeries
from collectors.common import LoadSpec, Settings
from collectors.common.bls import fetch_series, join_footnotes, parse_value, pct_change
from collectors.common.secrets import get_secret

_log = logging.getLogger(__name__)

TABLE = "bls_cpi.cpi_series"
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


def collect(settings: Settings) -> LoadSpec:
    today = date.today()
    end_year = today.year
    start_year = end_year - LOOKBACK_YEARS

    series_index = {s.series_id: s for s in CPI_SERIES}
    api_key = get_secret(settings.project_id, BLS_API_KEY_SECRET)

    by_series = fetch_series(
        list(series_index),
        start_year,
        end_year,
        api_key=api_key,
        calculations=True,
    )

    rows: list[dict] = []
    for series_id, points in by_series.items():
        meta = series_index[series_id]
        rows.extend(_rows_for_series(meta, points))

    _log.info(
        "CPI series fetched",
        extra={"extras": {"series": len(by_series), "rows": len(rows)}},
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _rows_for_series(meta: CpiSeries, points: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for point in points:
        obs_date = _period_to_date(point["year"], point["period"])
        if obs_date is None:
            continue  # skip annual averages (M13) and other non-monthly periods
        rows.append(
            {
                "series_id": meta.series_id,
                "item_code": meta.item_code,
                "description": meta.description,
                "seasonally_adjusted": meta.seasonally_adjusted,
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
        )
    return rows


def _period_to_date(year: str, period: str) -> date | None:
    if period.startswith("M"):
        month = int(period[1:])
        if 1 <= month <= 12:
            return date(int(year), month, 1)
    return None
