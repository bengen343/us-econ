import logging
from datetime import date

from google.cloud import bigquery

from collectors.bls_employment.series import EMPLOYMENT_SITUATION_SERIES
from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries
from collectors.common.secrets import get_secret

_log = logging.getLogger(__name__)

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
TABLE = "bls_employment.employment_situation"
LOOKBACK_YEARS = 3
BLS_API_KEY_SECRET = "bls-api-key"


SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("series_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("survey", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("units", "STRING"),
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("year", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("period", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("period_name", "STRING"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("footnotes", "STRING"),
]


def collect(settings: Settings) -> LoadSpec:
    today = date.today()
    if not _is_first_friday(today):
        _log.info(
            "skipping non-release Friday",
            extra={"extras": {"date": today.isoformat(), "weekday": today.strftime("%A")}},
        )
        return LoadSpec(table=TABLE, schema=SCHEMA, rows=[])

    end_year = today.year
    start_year = end_year - LOOKBACK_YEARS

    series_index = {s.series_id: s for s in EMPLOYMENT_SITUATION_SERIES}

    payload: dict = {
        "seriesid": list(series_index.keys()),
        "startyear": str(start_year),
        "endyear": str(end_year),
    }

    api_key = get_secret(settings.project_id, BLS_API_KEY_SECRET)
    if api_key:
        payload["registrationkey"] = api_key

    with client() as http:
        def call() -> dict:
            response = http.post(BLS_API_URL, json=payload)
            response.raise_for_status()
            return response.json()

        body = with_retries(call)

    if body.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(
            f"BLS API failure: status={body.get('status')!r} message={body.get('message')!r}"
        )

    rows: list[dict] = []
    for series in body["Results"]["series"]:
        meta = series_index[series["seriesID"]]
        for point in series["data"]:
            obs_date = _period_to_date(point["year"], point["period"])
            if obs_date is None:
                continue  # skip annual aggregates (M13) and other non-monthly periods
            rows.append(
                {
                    "series_id": meta.series_id,
                    "survey": meta.survey,
                    "description": meta.description,
                    "units": meta.units,
                    "observation_date": obs_date.isoformat(),
                    "year": int(point["year"]),
                    "period": point["period"],
                    "period_name": point.get("periodName"),
                    "value": _parse_value(point.get("value")),
                    "footnotes": _join_footnotes(point.get("footnotes", [])),
                }
            )

    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _is_first_friday(d: date) -> bool:
    return d.weekday() == 4 and d.day <= 7


def _period_to_date(year: str, period: str) -> date | None:
    if period.startswith("M"):
        month = int(period[1:])
        if 1 <= month <= 12:
            return date(int(year), month, 1)
        return None
    if period.startswith("Q"):
        quarter = int(period[1:])
        if 1 <= quarter <= 4:
            return date(int(year), (quarter - 1) * 3 + 1, 1)
        return None
    return None


def _parse_value(raw: str | None) -> float | None:
    if raw is None or raw in ("", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _join_footnotes(footnotes: list[dict]) -> str | None:
    parts = [fn.get("text") or fn.get("code") for fn in footnotes if fn]
    parts = [p for p in parts if p]
    return "; ".join(parts) if parts else None
