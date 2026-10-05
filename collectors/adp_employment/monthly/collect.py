import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta

import httpx
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "adp_employment.ner_history"
ARTIFACT_URL_TEMPLATE = "https://adpemploymentreport.com/artifacts/us_ner/{date}/ADP_NER_history.zip"
CSV_FILENAME = "ADP_NER_history.csv"

# ADP publishes on the Wednesday before the BLS jobs Friday, which can land in the
# prior month (e.g. 2026-09-30), so there is no fixed calendar rule. The job runs
# every Wednesday and probes the artifact URL for each recent date; the newest hit
# is loaded only if that vintage isn't already in the table. The lookback spans a
# week plus slack so a release on an off day or a failed run is picked up next week.
LOOKBACK_DAYS = 10

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("timestep", "STRING", mode="REQUIRED"),  # "M" or "W"
    bigquery.SchemaField("aggregation", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("category", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("ner", "FLOAT64"),
    bigquery.SchemaField("ner_sa", "FLOAT64"),
    bigquery.SchemaField("vintage_date", "DATE", mode="REQUIRED"),
]


def collect(settings: Settings) -> LoadSpec:
    today = date.today()
    with client() as http:
        found = _find_latest_artifact(http, today)
    if found is None:
        _log.info(
            "no ADP NER artifact in lookback window; skipping load",
            extra={"extras": {"date": today.isoformat(), "lookback_days": LOOKBACK_DAYS}},
        )
        return LoadSpec(table=TABLE, schema=SCHEMA, rows=[])

    zip_bytes, vintage = found
    if _vintage_loaded(settings, vintage):
        _log.info(
            "latest ADP NER vintage already loaded; skipping load",
            extra={"extras": {"vintage_date": vintage.isoformat()}},
        )
        return LoadSpec(table=TABLE, schema=SCHEMA, rows=[])

    csv_text = _extract_csv(zip_bytes)
    rows = _parse_csv(csv_text, vintage)

    _log.info(
        "ADP NER history parsed",
        extra={"extras": {"row_count": len(rows), "vintage_date": vintage.isoformat()}},
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _find_latest_artifact(http: httpx.Client, today: date) -> tuple[bytes, date] | None:
    """Newest history zip published in the last LOOKBACK_DAYS days, with its date."""
    for offset in range(LOOKBACK_DAYS + 1):
        candidate = today - timedelta(days=offset)
        body = _try_get(http, ARTIFACT_URL_TEMPLATE.format(date=candidate.strftime("%Y%m%d")))
        if body is not None:
            return body, candidate
    return None


def _vintage_loaded(settings: Settings, vintage: date) -> bool:
    bq = bigquery.Client(project=settings.project_id, location=settings.bq_location)
    job = bq.query(
        f"SELECT 1 FROM `{settings.project_id}.{TABLE}` WHERE vintage_date = @vintage LIMIT 1",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("vintage", "DATE", vintage)]
        ),
    )
    try:
        return any(True for _ in job.result())
    except NotFound:
        return False


def _try_get(http: httpx.Client, url: str) -> bytes | None:
    """Return zip bytes if published, None if not, raise on other errors.

    Unpublished dates come back as a 404 or, currently, as a 200 HTML page, so a
    body without the zip magic number also counts as not published."""

    def call() -> httpx.Response:
        response = http.get(url, headers={"Accept": "application/zip"})
        # Don't retry 404 — that's a "not yet published" signal we want to handle.
        if response.status_code == 404:
            return response
        response.raise_for_status()
        return response

    response = with_retries(call)
    if response.status_code == 404 or not response.content.startswith(b"PK\x03\x04"):
        return None
    return response.content


def _extract_csv(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        members = zf.namelist()
        if CSV_FILENAME not in members:
            raise RuntimeError(
                f"expected {CSV_FILENAME!r} inside ADP zip; got members={members!r}"
            )
        with zf.open(CSV_FILENAME) as f:
            return f.read().decode("utf-8-sig")


def _parse_csv(csv_text: str, vintage: date) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    expected = {"timestep", "agg_RIS", "category", "date", "NER", "NER_SA"}
    actual = set(reader.fieldnames or [])
    if not expected.issubset(actual):
        raise RuntimeError(
            f"ADP CSV missing expected columns; expected superset of {expected}, got {actual}"
        )

    rows: list[dict] = []
    for record in reader:
        obs = _parse_iso_date(record["date"])
        if obs is None:
            continue
        rows.append(
            {
                "timestep": record["timestep"],
                "aggregation": record["agg_RIS"],
                "category": record["category"],
                "observation_date": obs.isoformat(),
                "ner": _parse_float(record["NER"]),
                "ner_sa": _parse_float(record["NER_SA"]),
                "vintage_date": vintage.isoformat(),
            }
        )
    return rows


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None

