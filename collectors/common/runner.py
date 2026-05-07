import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from google.cloud import bigquery

from collectors.common import bigquery as bq
from collectors.common import storage
from collectors.common.config import Settings
from collectors.common.logging import configure_logging


@dataclass
class LoadSpec:
    """What a collector returns: rows + the BQ table they belong to + schema.

    When ``merge_keys`` is set, the runner upserts into the target table on those
    columns (latest run's row replaces the matching one; rows in the target with
    no match in this run are left untouched). When ``merge_keys`` is empty, rows
    are appended (the historical default for vintage-tagged collectors).
    """

    table: str  # "<dataset>.<table>", project comes from Settings
    schema: list[bigquery.SchemaField]
    rows: list[dict]
    merge_keys: list[str] = field(default_factory=list)


CollectResult = LoadSpec | list[LoadSpec]

FRAMEWORK_SCHEMA = [
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("ingestion_run_id", "STRING", mode="REQUIRED"),
]

DEFAULT_PARTITIONING = bigquery.TimePartitioning(
    type_=bigquery.TimePartitioningType.DAY,
    field="ingested_at",
)


def run_collector(source: str, collect: Callable[[Settings], CollectResult]) -> None:
    settings = Settings.from_env()
    run_id = _make_run_id()
    log = configure_logging(source, run_id)

    started = time.monotonic()
    log.info(f"collector start: {source}")

    result = collect(settings)
    specs = result if isinstance(result, list) else [result]

    now_iso = datetime.now(UTC).isoformat()
    for index, spec in enumerate(specs):
        _process_spec(
            settings=settings,
            log=log,
            source=source,
            run_id=run_id,
            now_iso=now_iso,
            spec=spec,
            spec_index=index,
            total_specs=len(specs),
        )

    log.info(
        "collector finished",
        extra={"extras": {"specs": len(specs), "duration_s": round(time.monotonic() - started, 2)}},
    )


def _process_spec(
    *,
    settings: Settings,
    log,
    source: str,
    run_id: str,
    now_iso: str,
    spec: LoadSpec,
    spec_index: int,
    total_specs: int,
) -> None:
    log.info(
        "spec fetched rows",
        extra={
            "extras": {
                "row_count": len(spec.rows),
                "table": spec.table,
                "spec_index": spec_index,
                "total_specs": total_specs,
                "mode": "merge" if spec.merge_keys else "append",
            }
        },
    )

    if not spec.rows:
        log.warning(
            "spec returned zero rows; skipping load",
            extra={"extras": {"table": spec.table}},
        )
        return

    for row in spec.rows:
        row["ingested_at"] = now_iso
        row["ingestion_run_id"] = run_id

    # GCS path includes the table when there are multiple specs so each lands in
    # its own object — easier to inspect later.
    table_slug = spec.table.replace(".", "_")
    suffix = f"/{table_slug}" if total_specs > 1 else ""
    object_path = f"{source}{suffix}/dt={now_iso[:10]}/run_{run_id}.jsonl"
    uri = storage.write_jsonl(
        project_id=settings.project_id,
        bucket=settings.raw_bucket,
        object_path=object_path,
        rows=spec.rows,
    )
    log.info("uploaded raw archive", extra={"extras": {"uri": uri, "table": spec.table}})

    full_table = f"{settings.project_id}.{spec.table}"
    full_schema = spec.schema + FRAMEWORK_SCHEMA

    if spec.merge_keys:
        job = bq.merge_jsonl_uri(
            project_id=settings.project_id,
            location=settings.bq_location,
            target_table=full_table,
            schema=full_schema,
            source_uri=uri,
            merge_keys=spec.merge_keys,
            time_partitioning=DEFAULT_PARTITIONING,
            staging_run_id=run_id,
        )
        log.info(
            "bq merge complete",
            extra={"extras": {"table": full_table, "merge_keys": spec.merge_keys, "stats": job}},
        )
    else:
        job = bq.load_jsonl_uri(
            project_id=settings.project_id,
            location=settings.bq_location,
            table=full_table,
            schema=full_schema,
            source_uri=uri,
            time_partitioning=DEFAULT_PARTITIONING,
        )
        log.info(
            "bq load complete",
            extra={"extras": {"table": full_table, "output_rows": job.output_rows}},
        )


def _make_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"
