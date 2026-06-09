"""Cloud Run Job entrypoint for the AAA gasoline next-day forecast.

Runs daily after the AAA scrape and the RBOB/WTI futures + EIA price collectors
land. Flow:

  1. Pull inputs from BigQuery (read-only) and compute the next-day AAA regular
     forecasts: the symmetric RBOB ECM (ecm_sym_rbob_v1) and the seasonal-EC +
     daily-momentum blend (ecm_seas_mom_v1), one row each per run.
  2. Ensure the output table + _current view exist.
  3. Upsert this run's row keyed by as_of_date (idempotent on same-day retry; a
     new row per data date preserves the forecast trajectory for later backtest).
     The _current view surfaces the latest generation per (target, model_version).

There is no release-window gate -- this is a daily forecast that simply re-runs
each day against the freshest AAA anchor and RBOB settle. Set DRY_RUN=1 to compute
+ log without writing (local validation; per repo convention forecasts write
BigQuery only from the deployed Job).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, date, datetime

from google.cloud import bigquery

from collectors.common.config import Settings
from collectors.common.logging import configure_logging
from forecasts.aaa_gasoline.next_day.production import config as cfg
from forecasts.aaa_gasoline.next_day.production import models


def _ensure_table_and_view(client: bigquery.Client) -> None:
    client.query(f"""
    CREATE TABLE IF NOT EXISTS `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` (
      target               STRING    NOT NULL,
      target_date          DATE      NOT NULL,
      as_of_date           DATE      NOT NULL,
      generated_at         TIMESTAMP NOT NULL,
      model_version        STRING    NOT NULL,
      horizon_days         INT64,
      forecast_value       FLOAT64,
      forecast_rounded     FLOAT64,
      anchor_price         FLOAT64,
      rbob_price           FLOAT64,
      equilibrium_price    FLOAT64,
      expected_weekly_move FLOAT64,
      forecast_sigma       FLOAT64,
      units                STRING,
      n_train              INT64,
      run_id               STRING
    )
    PARTITION BY target_date
    OPTIONS (description = 'AAA national-average regular next-day (h=1) forecast from '
      'the symmetric RBOB error-correction model; one row per (target, as_of_date, '
      'model_version), upserted daily.')
    """).result()
    # forecast_sigma was added after the table first shipped; no-op once present.
    client.query(
        f"ALTER TABLE `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` "
        f"ADD COLUMN IF NOT EXISTS forecast_sigma FLOAT64"
    ).result()

    client.query(f"""
    CREATE OR REPLACE VIEW `{cfg.PROJECT}.{cfg.OUTPUT_CURRENT_VIEW}` AS
    SELECT * EXCEPT(rn) FROM (
      SELECT *, ROW_NUMBER() OVER (
                  PARTITION BY target, model_version
                  ORDER BY as_of_date DESC, generated_at DESC) AS rn
      FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`
    ) WHERE rn = 1
    """).result()


def _ensure_dist_table_and_view(client: bigquery.Client) -> None:
    client.query(f"""
    CREATE TABLE IF NOT EXISTS `{cfg.PROJECT}.{cfg.OUTPUT_DIST_TABLE}` (
      target         STRING    NOT NULL,
      target_date    DATE      NOT NULL,
      as_of_date     DATE      NOT NULL,
      generated_at   TIMESTAMP NOT NULL,
      model_version  STRING    NOT NULL,
      point_forecast FLOAT64,
      sigma_daily    FLOAT64,
      bucket_low     FLOAT64,
      bucket_high    FLOAT64,
      bucket_mid     FLOAT64,
      probability    FLOAT64,
      run_id         STRING
    )
    PARTITION BY target_date
    OPTIONS (description = 'Next-day AAA regular predictive distribution: half-cent '
      'probability bands (Gaussian, centered on the point forecast). One row per band '
      'per (target, as_of_date, model_version), upserted daily.')
    """).result()

    client.query(f"""
    CREATE OR REPLACE VIEW `{cfg.PROJECT}.{cfg.OUTPUT_DIST_CURRENT_VIEW}` AS
    WITH latest AS (
      SELECT target, model_version, MAX(as_of_date) AS as_of_date
      FROM `{cfg.PROJECT}.{cfg.OUTPUT_DIST_TABLE}`
      GROUP BY target, model_version
    )
    SELECT d.* FROM `{cfg.PROJECT}.{cfg.OUTPUT_DIST_TABLE}` d
    JOIN latest USING (target, model_version, as_of_date)
    ORDER BY d.bucket_low
    """).result()


def _upsert(client: bigquery.Client, table: str, rows: list[dict], as_of: date) -> None:
    """Idempotent on same-day retry: clear this as_of_date, then insert."""
    client.query(
        f"DELETE FROM `{cfg.PROJECT}.{table}` WHERE as_of_date = @asof",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("asof", "DATE", as_of)]
        ),
    ).result()
    client.load_table_from_json(
        rows,
        f"{cfg.PROJECT}.{table}",
        job_config=bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND),
    ).result()


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    run_id = _run_id()
    log = configure_logging("forecast.aaa_gasoline", run_id)
    started = time.monotonic()

    settings = Settings.from_env()
    client = bigquery.Client(project=settings.project_id)

    log.info("computing forecast")
    forecasts = models.compute(client)
    if not forecasts:
        log.warning("no forecast produced (inputs incomplete); skipping")
        return

    generated_at = datetime.now(UTC)
    as_of = forecasts[0].as_of_date
    audit = [
        {
            "target": f.target,
            "model_version": f.model_version,
            "target_date": f.target_date.isoformat(),
            "value": f.value_rounded,
            "anchor": round(f.anchor_price, 3),
            "equilibrium": round(f.equilibrium_price, 3),
            "sigma": round(f.sigma_daily, 4),
            "n_buckets": len(f.distribution),
        }
        for f in forecasts
    ]
    log.info("forecast computed", extra={"extras": {"as_of": as_of.isoformat(), "rows": audit}})

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        log.info(
            "DRY_RUN: skipping write",
            extra={"extras": {"duration_s": round(time.monotonic() - started, 2)}},
        )
        return

    rows = [
        {
            "target": f.target,
            "target_date": f.target_date.isoformat(),
            "as_of_date": f.as_of_date.isoformat(),
            "generated_at": generated_at.isoformat(),
            "model_version": f.model_version,
            "horizon_days": f.horizon_days,
            "forecast_value": f.value,
            "forecast_rounded": f.value_rounded,
            "anchor_price": f.anchor_price,
            "rbob_price": f.rbob_price,
            "equilibrium_price": f.equilibrium_price,
            "expected_weekly_move": f.expected_weekly_move,
            "forecast_sigma": f.sigma_daily,
            "units": f.units,
            "n_train": f.n_train,
            "run_id": run_id,
        }
        for f in forecasts
    ]
    dist_rows = [
        {
            "target": f.target,
            "target_date": f.target_date.isoformat(),
            "as_of_date": f.as_of_date.isoformat(),
            "generated_at": generated_at.isoformat(),
            "model_version": f.model_version,
            "point_forecast": f.value,
            "sigma_daily": f.sigma_daily,
            "bucket_low": b.low,
            "bucket_high": b.high,
            "bucket_mid": b.mid,
            "probability": b.prob,
            "run_id": run_id,
        }
        for f in forecasts
        for b in f.distribution
    ]
    _ensure_table_and_view(client)
    _ensure_dist_table_and_view(client)
    _upsert(client, cfg.OUTPUT_TABLE, rows, as_of)
    _upsert(client, cfg.OUTPUT_DIST_TABLE, dist_rows, as_of)
    log.info(
        "forecast rows upserted",
        extra={
            "extras": {
                "table": cfg.OUTPUT_TABLE,
                "n": len(rows),
                "dist_table": cfg.OUTPUT_DIST_TABLE,
                "dist_n": len(dist_rows),
                "duration_s": round(time.monotonic() - started, 2),
            }
        },
    )


if __name__ == "__main__":
    main()
