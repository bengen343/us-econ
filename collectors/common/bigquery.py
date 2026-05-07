from google.cloud import bigquery


def load_jsonl_uri(
    *,
    project_id: str,
    location: str,
    table: str,
    schema: list[bigquery.SchemaField],
    source_uri: str,
    time_partitioning: bigquery.TimePartitioning | None = None,
    clustering_fields: list[str] | None = None,
) -> bigquery.LoadJob:
    """Load a JSON-NL file from GCS into a BigQuery table (append). Free batch load."""
    client = bigquery.Client(project=project_id, location=location)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        schema=schema,
        time_partitioning=time_partitioning,
        clustering_fields=clustering_fields,
        ignore_unknown_values=False,
    )

    job = client.load_table_from_uri(source_uri, table, job_config=job_config)
    job.result()
    return job


def merge_jsonl_uri(
    *,
    project_id: str,
    location: str,
    target_table: str,
    schema: list[bigquery.SchemaField],
    source_uri: str,
    merge_keys: list[str],
    time_partitioning: bigquery.TimePartitioning | None = None,
    clustering_fields: list[str] | None = None,
    staging_run_id: str,
) -> dict:
    """
    Upsert rows from a GCS JSON-NL file into ``target_table`` keyed on ``merge_keys``.

    Strategy:
      1. Ensure the target table exists (creates an empty one if not).
      2. Load source into a per-run staging table (WRITE_TRUNCATE).
      3. MERGE staging into target. Matching rows have all non-key columns
         overwritten; non-matching staging rows are inserted; rows in target
         with no match in staging are left untouched.
      4. Drop the staging table.

    Nullable key columns are matched with COALESCE(...,'') to handle NULL = NULL
    correctly (BigQuery's = operator returns NULL for NULL operands, breaking
    MERGE joins otherwise).
    """
    if not merge_keys:
        raise ValueError("merge_keys must be non-empty for merge_jsonl_uri")

    client = bigquery.Client(project=project_id, location=location)

    # 1. Make sure target exists with the right schema (no-op on subsequent runs).
    target_ref = bigquery.Table(target_table, schema=schema)
    if time_partitioning is not None:
        target_ref.time_partitioning = time_partitioning
    if clustering_fields:
        target_ref.clustering_fields = clustering_fields
    client.create_table(target_ref, exists_ok=True)

    # 2. Load into a per-run staging table.
    staging_short = staging_run_id.replace("-", "_")[:16]
    staging_table = f"{target_table}__staging_{staging_short}"
    staging_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        schema=schema,
        ignore_unknown_values=False,
    )
    load_job = client.load_table_from_uri(source_uri, staging_table, job_config=staging_config)
    load_job.result()

    # 3. MERGE.
    schema_keys = {field.name for field in schema}
    missing = [k for k in merge_keys if k not in schema_keys]
    if missing:
        raise ValueError(f"merge_keys not in schema: {missing}")

    nullable_keys = {f.name for f in schema if f.mode != "REQUIRED"} & set(merge_keys)
    on_clauses = []
    for key in merge_keys:
        if key in nullable_keys:
            on_clauses.append(f"COALESCE(T.`{key}`, '') = COALESCE(S.`{key}`, '')")
        else:
            on_clauses.append(f"T.`{key}` = S.`{key}`")
    on_sql = "\n  AND ".join(on_clauses)

    update_columns = [f.name for f in schema if f.name not in merge_keys]
    set_sql = ",\n    ".join(f"T.`{c}` = S.`{c}`" for c in update_columns)

    insert_columns = [f.name for f in schema]
    insert_cols_sql = ", ".join(f"`{c}`" for c in insert_columns)
    insert_vals_sql = ", ".join(f"S.`{c}`" for c in insert_columns)

    merge_sql = f"""
MERGE `{target_table}` T
USING `{staging_table}` S
ON {on_sql}
WHEN MATCHED THEN UPDATE SET
    {set_sql}
WHEN NOT MATCHED THEN
  INSERT ({insert_cols_sql}) VALUES ({insert_vals_sql})
""".strip()

    merge_job = client.query(merge_sql)
    merge_job.result()
    stats = {
        "dml_inserted_rows": merge_job.num_dml_affected_rows,
        "merge_total_rows": merge_job.num_dml_affected_rows,
    }

    # 4. Drop staging.
    client.delete_table(staging_table, not_found_ok=True)

    return stats
