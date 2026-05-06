import csv
import io
import logging
import re
import zipfile
from datetime import date, datetime

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "adp_employment.ner_history"
ARTIFACT_URL_TEMPLATE = "https://adpemploymentreport.com/artifacts/us_ner/{date}/ADP_NER_history.zip"
MEDIA_CENTER_URL = "https://mediacenter.adp.com/workforce-data-releases"
CSV_FILENAME = "ADP_NER_history.csv"

# Latest non-preliminary monthly NER press release URL on the media center.
# URLs look like:
#   /2026-05-06-ADP-National-Employment-Report-Private-Sector-Employment-...
# We exclude any URL containing "Preliminary".
_MONTHLY_RELEASE_URL_RE = re.compile(
    r"https?://mediacenter\.adp\.com/(\d{4})-(\d{2})-(\d{2})-ADP-National-Employment-Report-(?!Preliminary)[^\"'\s]+"
)

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
    if not _is_first_wednesday(today):
        _log.info(
            "skipping non-release weekday",
            extra={"extras": {"date": today.isoformat(), "weekday": today.strftime("%A")}},
        )
        return LoadSpec(table=TABLE, schema=SCHEMA, rows=[])

    with client() as http:
        zip_bytes, vintage = _download_history_zip(http, today)

    csv_text = _extract_csv(zip_bytes)
    rows = _parse_csv(csv_text, vintage)

    _log.info(
        "ADP NER history parsed",
        extra={"extras": {"row_count": len(rows), "vintage_date": vintage.isoformat()}},
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _download_history_zip(http: httpx.Client, today: date) -> tuple[bytes, date]:
    """Try today's date in the artifact URL; fall back to scraping the media center
    for the latest monthly NER press release date and retrying."""
    primary_url = ARTIFACT_URL_TEMPLATE.format(date=today.strftime("%Y%m%d"))
    primary = _try_get(http, primary_url)
    if primary is not None:
        return primary, today

    _log.warning(
        "primary artifact URL not found; scraping media center for latest release date",
        extra={"extras": {"primary_url": primary_url}},
    )
    fallback_date = _discover_latest_release_date(http)
    fallback_url = ARTIFACT_URL_TEMPLATE.format(date=fallback_date.strftime("%Y%m%d"))
    fallback = _try_get(http, fallback_url)
    if fallback is None:
        raise RuntimeError(
            f"ADP NER artifact not found at primary {primary_url!r} or fallback {fallback_url!r}"
        )
    return fallback, fallback_date


def _try_get(http: httpx.Client, url: str) -> bytes | None:
    """Return body bytes on 200, None on 404, raise on other errors."""

    def call() -> httpx.Response:
        response = http.get(url, headers={"Accept": "application/zip"})
        # Don't retry 404 — that's a "not yet published" signal we want to handle.
        if response.status_code == 404:
            return response
        response.raise_for_status()
        return response

    response = with_retries(call)
    if response.status_code == 404:
        return None
    return response.content


def _discover_latest_release_date(http: httpx.Client) -> date:
    def call() -> str:
        response = http.get(MEDIA_CENTER_URL, headers={"Accept": "text/html"})
        response.raise_for_status()
        return response.text

    html = with_retries(call)
    match = _MONTHLY_RELEASE_URL_RE.search(html)
    if match is None:
        raise RuntimeError(
            "could not find a monthly ADP NER release URL on media center page"
        )
    year, month, day = (int(g) for g in match.groups())
    return date(year, month, day)


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


def _is_first_wednesday(d: date) -> bool:
    return d.weekday() == 2 and d.day <= 7
