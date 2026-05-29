"""Cloud Run Job entrypoint for the ADP headline Pulse-bridge forecast.

Designed to run after every weekly NER Pulse release (Tuesdays) so the forecast
for the next unreleased monthly headline is revised as the target month's weeks
land. Flow:

  1. Pull freshest monthly SA level + weekly Pulse from BigQuery (read-only).
  2. Compute the blended forecast for the next unreleased month.
  3. Ensure the output table + _current view exist.
  4. Upsert one revision row keyed by (target_month, as_of_pulse_week,
     model_version) — idempotent on retry, append-on-new-Pulse-week. The
     _current view surfaces the latest generation per target_month.

Set DRY_RUN=1 to compute + log without writing (used for local validation;
per repo convention forecasts write BigQuery only from the deployed Job).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime

from google.cloud import bigquery

from collectors.common.config import Settings
from collectors.common.logging import configure_logging
from forecasts.adp_employment.national_monthly.pulse_bridge import config as cfg
from forecasts.adp_employment.national_monthly.pulse_bridge import data, model
from forecasts.adp_employment.national_monthly.pulse_bridge.model import Forecast


def _ensure_table_and_view(client: bigquery.Client) -> None:
    client.query(f"""
    CREATE TABLE IF NOT EXISTS `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` (
      target_month      DATE      NOT NULL,
      generated_at      TIMESTAMP NOT NULL,
      as_of_pulse_week  DATE,
      headline_forecast FLOAT64,
      prior_component   FLOAT64,
      pulse_component   FLOAT64,
      blend_weight      FLOAT64,
      pulse_run_rate    FLOAT64,
      pulse_implied     FLOAT64,
      pulse_weeks_used  INT64,
      expected_weeks    INT64,
      calib_scale       FLOAT64,
      calib_raw_scale   FLOAT64,
      calib_n_months    INT64,
      model_version     STRING,
      run_id            STRING
    )
    PARTITION BY target_month
    OPTIONS (description = 'ADP national-monthly headline Pulse-bridge forecast; '
      'append-only weekly revisions, one row per (target_month, as_of_pulse_week, model_version).')
    """).result()

    client.query(f"""
    CREATE OR REPLACE VIEW `{cfg.PROJECT}.{cfg.OUTPUT_CURRENT_VIEW}` AS
    SELECT * EXCEPT(rn) FROM (
      SELECT *, ROW_NUMBER() OVER (
                  PARTITION BY target_month
                  ORDER BY generated_at DESC) AS rn
      FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`
    ) WHERE rn = 1
    """).result()


def _upsert(client: bigquery.Client, fc: Forecast, generated_at: datetime,
            run_id: str) -> None:
    """Delete any prior row for this refresh key, then insert the fresh one."""
    as_of_week = (fc.latest_pulse_week.date().isoformat()
                  if fc.latest_pulse_week is not None else None)
    params = [
        bigquery.ScalarQueryParameter("target_month", "DATE", fc.target_month.date()),
        bigquery.ScalarQueryParameter("as_of_pulse_week", "DATE", as_of_week),
        bigquery.ScalarQueryParameter("model_version", "STRING", cfg.MODEL_VERSION),
    ]
    client.query(
        f"""DELETE FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`
            WHERE target_month = @target_month
              AND model_version = @model_version
              AND as_of_pulse_week IS NOT DISTINCT FROM @as_of_pulse_week""",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()

    insert_params = params + [
        bigquery.ScalarQueryParameter("generated_at", "TIMESTAMP", generated_at),
        bigquery.ScalarQueryParameter("headline_forecast", "FLOAT64", fc.headline_forecast),
        bigquery.ScalarQueryParameter("prior_component", "FLOAT64", fc.prior_component),
        bigquery.ScalarQueryParameter(
            "pulse_component", "FLOAT64", _nan_to_none(fc.pulse_component)),
        bigquery.ScalarQueryParameter("blend_weight", "FLOAT64", fc.blend_weight),
        bigquery.ScalarQueryParameter("pulse_run_rate", "FLOAT64", _nan_to_none(fc.pulse_run_rate)),
        bigquery.ScalarQueryParameter("pulse_implied", "FLOAT64", _nan_to_none(fc.pulse_implied)),
        bigquery.ScalarQueryParameter("pulse_weeks_used", "INT64", fc.pulse_weeks_used),
        bigquery.ScalarQueryParameter("expected_weeks", "INT64", fc.expected_weeks),
        bigquery.ScalarQueryParameter("calib_scale", "FLOAT64", fc.calib_scale),
        bigquery.ScalarQueryParameter("calib_raw_scale", "FLOAT64", fc.calib_raw_scale),
        bigquery.ScalarQueryParameter("calib_n_months", "INT64", fc.calib_n_months),
        bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
    ]
    client.query(
        f"""INSERT INTO `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` (
              target_month, generated_at, as_of_pulse_week, headline_forecast,
              prior_component, pulse_component, blend_weight, pulse_run_rate,
              pulse_implied, pulse_weeks_used, expected_weeks, calib_scale,
              calib_raw_scale, calib_n_months, model_version, run_id)
            VALUES (
              @target_month, @generated_at, @as_of_pulse_week, @headline_forecast,
              @prior_component, @pulse_component, @blend_weight, @pulse_run_rate,
              @pulse_implied, @pulse_weeks_used, @expected_weeks, @calib_scale,
              @calib_raw_scale, @calib_n_months, @model_version, @run_id)""",
        job_config=bigquery.QueryJobConfig(query_parameters=insert_params),
    ).result()


def _nan_to_none(x: float) -> float | None:
    return None if x != x else x  # NaN != NaN


def _run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    run_id = _run_id()
    log = configure_logging("forecast.adp_pulse_bridge", run_id)
    started = time.monotonic()

    settings = Settings.from_env()
    if settings.project_id != cfg.PROJECT:
        log.warning("project mismatch", extra={"extras": {
            "settings_project": settings.project_id, "code_project": cfg.PROJECT}})
    client = bigquery.Client(project=settings.project_id)

    log.info("pulling inputs from BigQuery")
    monthly = data.pull_monthly(client)
    pulse = data.pull_pulse(client)
    log.info("inputs loaded", extra={"extras": {
        "monthly_rows": len(monthly),
        "monthly_last": monthly["month"].max().date().isoformat(),
        "pulse_rows": len(pulse),
        "pulse_last": (pulse["week_ending"].max().date().isoformat()
                       if not pulse.empty else None),
    }})

    fc = model.forecast(monthly, pulse)
    generated_at = datetime.now(UTC)
    audit = {
        "target_month": fc.target_month.date().isoformat(),
        "headline_forecast": round(fc.headline_forecast),
        "prior_component": round(fc.prior_component),
        "pulse_component": (None if fc.pulse_component != fc.pulse_component
                            else round(fc.pulse_component)),
        "blend_weight": round(fc.blend_weight, 3),
        "pulse_weeks_used": fc.pulse_weeks_used,
        "expected_weeks": fc.expected_weeks,
        "calib_scale": round(fc.calib_scale, 4),
        "calib_n_months": fc.calib_n_months,
        "latest_pulse_week": (fc.latest_pulse_week.date().isoformat()
                              if fc.latest_pulse_week is not None else None),
    }
    log.info("forecast computed", extra={"extras": audit})

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        log.info("DRY_RUN: skipping table ensure + upsert", extra={"extras": {
            "duration_s": round(time.monotonic() - started, 2), **audit}})
        return

    _ensure_table_and_view(client)
    _upsert(client, fc, generated_at, run_id)
    log.info("forecast row upserted", extra={"extras": {
        "table": cfg.OUTPUT_TABLE,
        "duration_s": round(time.monotonic() - started, 2), **audit}})


if __name__ == "__main__":
    main()
