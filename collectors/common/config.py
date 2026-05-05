import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_id: str
    raw_bucket: str
    bq_location: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            project_id=_required("GCP_PROJECT"),
            raw_bucket=_required("RAW_BUCKET"),
            bq_location=os.environ.get("BQ_LOCATION", "US"),
        )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
