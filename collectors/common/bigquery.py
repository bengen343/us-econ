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
