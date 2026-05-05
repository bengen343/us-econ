import io
import json
from collections.abc import Iterable

from google.cloud import storage


def write_jsonl(
    *,
    project_id: str,
    bucket: str,
    object_path: str,
    rows: Iterable[dict],
) -> str:
    """Upload rows as newline-delimited JSON to GCS. Returns the gs:// URI."""
    client = storage.Client(project=project_id)
    blob = client.bucket(bucket).blob(object_path)

    buf = io.BytesIO()
    for row in rows:
        buf.write(json.dumps(row, default=str).encode("utf-8"))
        buf.write(b"\n")
    buf.seek(0)

    blob.upload_from_file(buf, content_type="application/x-ndjson")
    return f"gs://{bucket}/{object_path}"
