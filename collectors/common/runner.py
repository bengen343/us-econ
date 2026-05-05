import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from google.cloud import bigquery

from collectors.common import bigquery as bq
from collectors.common import storage
from collectors.common.config import Settings
from collectors.common.logging import configure_logging


@dataclass
class LoadSpec:
    """What a collector returns: rows + the BQ table they belong to + schema (without framework cols)."""

    table: str  # "<dataset>.<table>", project comes from Settings
    schema: list[bigquery.SchemaField]
    rows: list[dict]


FRAMEWORK_SCHEMA = [
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("ingestion_run_id", "STRING", mode="REQUIRED"),
]

DEFAULT_PARTITIONING = bigquery.TimePartitioning(
    type_=bigquery.TimePartitioningType.DAY,
    field="ingested_at",
)


def run_collector(source: str, collect: Callable[[Settings], LoadSpec]) -> None:
    settings = Settings.from_env()
    run_id = _make_run_id()
    log = configure_logging(source, run_id)

    started = time.monotonic()
    log.info(f"collector start: {source}")

    spec = collect(settings)
    log.info(
        "collector fetched rows",
        extra={"extras": {"row_count": len(spec.rows), "table": spec.table}},
    )

    if not spec.rows:
        log.warning("collector returned zero rows; skipping load")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    for row in spec.rows:
        row["ingested_at"] = now_iso
        row["ingestion_run_id"] = run_id

    object_path = f"{source}/dt={now_iso[:10]}/run_{run_id}.jsonl"
    uri = storage.write_jsonl(
        project_id=settings.project_id,
        bucket=settings.raw_bucket,
        object_path=object_path,
        rows=spec.rows,
    )
    log.info("uploaded raw archive", extra={"extras": {"uri": uri}})

    full_table = f"{settings.project_id}.{spec.table}"
    job = bq.load_jsonl_uri(
        project_id=settings.project_id,
        location=settings.bq_location,
        table=full_table,
        schema=spec.schema + FRAMEWORK_SCHEMA,
        source_uri=uri,
        time_partitioning=DEFAULT_PARTITIONING,
    )
    log.info(
        "bq load complete",
        extra={
            "extras": {
                "table": full_table,
                "output_rows": job.output_rows,
                "duration_s": round(time.monotonic() - started, 2),
            }
        },
    )


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"
