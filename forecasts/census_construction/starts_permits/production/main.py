"""Cloud Run Job entrypoint for the housing starts + permits forecasts.

Runs daily through the pre-release window (the New Residential Construction
report for the prior month drops ~the 16th-19th at 08:30 ET). Flow:

  1. Pull inputs from BigQuery (read-only) and compute the next-month starts
     and permits nowcasts (SAAR level and m/m % each). Whichever target's
     regressors are incomplete is skipped (the permits spec completes right
     after the prior release; the starts spec waits for the month-M NOAA
     temperature, posted ~the 8th).
  2. Ensure the output table + _current view exist.
  3. Upsert this run's rows keyed by as_of_date (idempotent on same-day
     retry; a new row per day preserves the revision trajectory). The
     _current view surfaces the latest generation per (target, target_month,
     model_version).

Set DRY_RUN=1 to compute + log without writing (local validation; per repo
convention forecasts write BigQuery only from the deployed Job).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, date, datetime

from google.cloud import bigquery

from collectors.common.config import Settings
from collectors.common.logging import configure_logging
from forecasts.census_construction.starts_permits.production import config as cfg
from forecasts.census_construction.starts_permits.production import models

FORECAST_WINDOW_LAST_DAY = 20  # the NRC release lands ~the 16th-19th


def _in_forecast_window(d: date) -> bool:
    """Through the release + a buffer day. The job re-nowcasts the
    next-to-be-released month each day as its inputs accrue."""
    return d.day <= FORECAST_WINDOW_LAST_DAY


def _ensure_table_and_view(client: bigquery.Client) -> None:
    client.query(f"""
    CREATE TABLE IF NOT EXISTS `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` (
      target          STRING    NOT NULL,
      target_month    DATE      NOT NULL,
      as_of_date      DATE      NOT NULL,
      generated_at    TIMESTAMP NOT NULL,
      model_version   STRING    NOT NULL,
      forecast_value  FLOAT64,
      forecast_rounded FLOAT64,
      units           STRING,
      n_train         INT64,
      run_id          STRING
    )
    PARTITION BY target_month
    OPTIONS (description = 'Housing starts + building permits forecasts (SAAR '
      'level and m/m %) for the next New Residential Construction release -- '
      'starts from the permits/starts ECM + HMI + temperature OLS, permits '
      'from the SF/MF-split AR; append-only pre-release revisions, one row '
      'per (target, target_month, as_of_date, model_version).')
    """).result()

    client.query(f"""
    CREATE OR REPLACE VIEW `{cfg.PROJECT}.{cfg.OUTPUT_CURRENT_VIEW}` AS
    SELECT * EXCEPT(rn) FROM (
      SELECT *, ROW_NUMBER() OVER (
                  PARTITION BY target, target_month, model_version
                  ORDER BY as_of_date DESC, generated_at DESC) AS rn
      FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`
    ) WHERE rn = 1
    """).result()


def _upsert(client: bigquery.Client, rows: list[dict], as_of: date) -> None:
    """Idempotent on same-day retry: clear this as_of_date, then insert."""
    client.query(
        f"DELETE FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` WHERE as_of_date = @asof",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("asof", "DATE", as_of)]
        ),
    ).result()
    client.load_table_from_json(
        rows,
        f"{cfg.PROJECT}.{cfg.OUTPUT_TABLE}",
        job_config=bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND),
    ).result()


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    run_id = _run_id()
    log = configure_logging("forecast.starts_permits", run_id)
    started = time.monotonic()
    today = date.today()

    if not _in_forecast_window(today):
        log.info(
            "outside the pre-release forecast window; skipping",
            extra={"extras": {"date": today.isoformat(), "day": today.day}},
        )
        return

    settings = Settings.from_env()
    client = bigquery.Client(project=settings.project_id)

    log.info("computing forecasts")
    forecasts = models.compute(client)
    if not forecasts:
        log.warning("no target computable (inputs incomplete); skipping")
        return

    generated_at = datetime.now(UTC)
    audit = [
        {"target": f.target, "month": f.target_month.date().isoformat(), "value": f.value_rounded}
        for f in forecasts
    ]
    log.info("forecasts computed", extra={"extras": {"n": len(forecasts), "rows": audit}})

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        log.info(
            "DRY_RUN: skipping write",
            extra={"extras": {"duration_s": round(time.monotonic() - started, 2)}},
        )
        return

    rows = [
        {
            "target": f.target,
            "target_month": f.target_month.date().isoformat(),
            "as_of_date": today.isoformat(),
            "generated_at": generated_at.isoformat(),
            "model_version": f.model_version,
            "forecast_value": f.value,
            "forecast_rounded": f.value_rounded,
            "units": f.units,
            "n_train": f.n_train,
            "run_id": run_id,
        }
        for f in forecasts
    ]
    _ensure_table_and_view(client)
    _upsert(client, rows, today)
    log.info(
        "forecast rows upserted",
        extra={
            "extras": {
                "table": cfg.OUTPUT_TABLE,
                "n": len(rows),
                "duration_s": round(time.monotonic() - started, 2),
            }
        },
    )


if __name__ == "__main__":
    main()
