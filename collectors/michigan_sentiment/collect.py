"""University of Michigan Surveys of Consumers collector.

Lands the three headline indexes -- Index of Consumer Sentiment, Current
Economic Conditions, Index of Consumer Expectations -- with the
preliminary/final release distinction modeled explicitly, into
``michigan_sentiment.surveys_of_consumers``.

Two sources per run:

  1. **Homepage capture** (sca.isr.umich.edu): whatever release is currently
     shown ("Preliminary|Final Results for <Month>"). This is the ONLY public
     source of preliminary values, which are replaced on the site when the
     final lands ~2 weeks later. Releases are Fridays 10:00 ET (prelim ~2nd
     Friday of the survey month, final ~4th); the job runs every Friday just
     after.
  2. **Official final-history CSVs** (files/tbmics.csv, files/tbmiccice.csv):
     the full final history from 1952/1978, re-ingested every run so missed
     final captures self-heal. CSV months not yet finalized (the CSVs can
     carry the current month's preliminary value during prelim weeks) are
     excluded -- a CSV month only loads as "final" once the homepage shows
     its final (or a later month).

Rows are MERGE-upserted on (measure, release_type, observation_month):
re-running on an unchanged page rewrites identical rows, and prelim rows are
never clobbered by finals (distinct release_type). A missed *preliminary*
Friday does NOT self-heal from the site -- recover it from a Wayback snapshot
of the homepage (the 2026 backfill did exactly this) before the final
replaces it, or accept the gap.

The index history itself is never revised once final (the prelim->final
restatement IS the revision), so the merge upsert leaves history stable.
"""

import logging
from datetime import date

import httpx
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries
from collectors.michigan_sentiment.parser import parse_csv_months, parse_homepage

_log = logging.getLogger(__name__)

HOMEPAGE = "https://www.sca.isr.umich.edu/"
CSV_ICS = "https://www.sca.isr.umich.edu/files/tbmics.csv"  # Month,YYYY,ICS_ALL
CSV_ICC_ICE = "https://www.sca.isr.umich.edu/files/tbmiccice.csv"  # Month,YYYY,ICC,ICE
TABLE = "michigan_sentiment.surveys_of_consumers"
UNITS = "index (1966:Q1=100)"

SCHEMA: list[bigquery.SchemaField] = [
    # sentiment | current_conditions | expectations
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    # preliminary | final
    bigquery.SchemaField("release_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
    # Where this row came from: sca_homepage | sca_csv | backfill (one-offs).
    bigquery.SchemaField("source", "STRING"),
    # Homepage rows: the capture date (the release Friday when run on
    # schedule). CSV/backfill rows: NULL.
    bigquery.SchemaField("release_date", "DATE"),
]

MERGE_KEYS = ["measure", "release_type", "observation_month"]


def collect(settings: Settings) -> LoadSpec:
    with client() as http:
        page = parse_homepage(_get(http, HOMEPAGE))
        ics_rows = parse_csv_months(_get(http, CSV_ICS))
        icc_ice_rows = parse_csv_months(_get(http, CSV_ICC_ICE))

    for warning in page.warnings:
        _log.warning("homepage parse check", extra={"extras": {"detail": warning}})
    _log.info(
        "homepage release parsed",
        extra={
            "extras": {
                "release_type": page.release_type,
                "observation_month": page.observation_month.isoformat(),
                "values": page.values,
            }
        },
    )

    rows = [
        {
            "measure": measure,
            "release_type": page.release_type,
            "observation_month": page.observation_month.isoformat(),
            "value": value,
            "units": UNITS,
            "source": "sca_homepage",
            "release_date": date.today().isoformat(),
        }
        for measure, value in page.values.items()
    ]

    # The CSVs are final history EXCEPT possibly the homepage month itself
    # while its release is still preliminary: a month only loads as "final"
    # once the homepage shows its final (or has moved past it).
    if page.release_type == "final":
        last_final = page.observation_month
    else:
        last_final = _prior_month(page.observation_month)
    csv_rows = [
        *_csv_rows(ics_rows, ["sentiment"], last_final),
        *_csv_rows(icc_ice_rows, ["current_conditions", "expectations"], last_final),
    ]
    _log.info(
        "final-history CSVs parsed",
        extra={"extras": {"rows": len(csv_rows), "last_final": str(last_final)}},
    )

    # When the homepage shows a FINAL, its rows and the CSV's rows for that
    # month share merge keys, and two source rows per key break the BigQuery
    # MERGE ("UPDATE/MERGE must match at most one source row for each target
    # row" -- same failure mode as the EIA duplicates). Collapse on the merge
    # keys; homepage rows are listed last so they win (they carry
    # release_date).
    return LoadSpec(
        table=TABLE, schema=SCHEMA, rows=_dedupe(csv_rows + rows), merge_keys=MERGE_KEYS
    )


def _dedupe(rows: list[dict]) -> list[dict]:
    """Collapse duplicate merge-key rows, last occurrence wins."""
    by_key: dict[tuple, dict] = {}
    for row in rows:
        by_key[tuple(row[key] for key in MERGE_KEYS)] = row
    dropped = len(rows) - len(by_key)
    if dropped:
        _log.info("dropped duplicate merge-key rows", extra={"extras": {"dropped": dropped}})
    return list(by_key.values())


def _prior_month(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def _csv_rows(
    parsed: list[tuple[date, list[float | None]]],
    measures: list[str],
    last_final: date,
) -> list[dict]:
    """CSV (month, values) pairs -> final rows, excluding months past the
    last finalized month."""
    rows: list[dict] = []
    for month, values in parsed:
        if month > last_final:
            continue
        # strict=False: a ragged CSV row (e.g. a missing trailing comma in the
        # ICC/ICE file) drops the absent measure rather than failing the run.
        for measure, value in zip(measures, values, strict=False):
            if value is None:
                continue
            rows.append(
                {
                    "measure": measure,
                    "release_type": "final",
                    "observation_month": month.isoformat(),
                    "value": value,
                    "units": UNITS,
                    "source": "sca_csv",
                    "release_date": None,
                }
            )
    return rows


def _get(http: httpx.Client, url: str) -> str:
    def call() -> str:
        response = http.get(url, headers={"Accept": "text/html,text/csv"})
        response.raise_for_status()
        return response.text

    return with_retries(call)
