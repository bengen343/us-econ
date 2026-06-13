"""Cloud Run Job entrypoint for the RCP Friday approval-average forecast.

Forecasts the RCP presidential-approval average for the upcoming Friday. Runs
daily Sat..Thu (horizon h=6..1) so the call is refined as the week's polls land;
on Fridays the target realises at the morning capture, so the job idles. The
forecast is BigQuery-only (it reads the collector's snapshot table and writes the
forecast table) — it never touches realclearpolling.com, so unlike the off-platform
collector it runs on Cloud Run.

Flow:
  1. Pull the snapshot history from BigQuery (read-only) and build the as-of
     window / published-average truth / per-pollster release history.
  2. Compute the horizon-blend forecast for the upcoming Friday.
  3. Ensure the output table + _current view exist.
  4. Upsert this run's row keyed by (target_friday, as_of_date, model_version) —
     idempotent on same-day retry, one new revision row per day as the horizon
     shrinks. The _current view surfaces the latest as_of per target_friday.

Set DRY_RUN=1 to compute + log without writing (local validation; per repo
convention forecasts write BigQuery only from the deployed Job).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from collectors.common.config import Settings
from collectors.common.logging import configure_logging
from forecasts.rcp_potus_approval.friday_average import data, model
from forecasts.rcp_potus_approval.friday_average.model import Forecast
from forecasts.rcp_potus_approval.friday_average.production import config as cfg

TZ = ZoneInfo("America/Denver")


def _upcoming_friday(today: date) -> date:
    """The next Friday strictly after ``today`` (so Friday targets the following week)."""
    return today + timedelta(days=((4 - today.weekday()) % 7) or 7)


def _ensure_table_and_view(client: bigquery.Client) -> None:
    client.query(f"""
    CREATE TABLE IF NOT EXISTS `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` (
      target_friday         DATE      NOT NULL,
      as_of_date            DATE      NOT NULL,
      generated_at          TIMESTAMP NOT NULL,
      horizon_days          INT64,
      forecast              FLOAT64,
      forecast_rounded      FLOAT64,
      carry_forward         FLOAT64,
      drift_corrected_carry FLOAT64,
      structural_mean       FLOAT64,
      drift_per_day         FLOAT64,
      band_lo               FLOAT64,
      band_hi               FLOAT64,
      n_window              INT64,
      model_version         STRING,
      run_id                STRING
    )
    PARTITION BY target_friday
    OPTIONS (description = 'RCP presidential-approval average forecast for the upcoming '
      'Friday; horizon-weighted blend of drift-corrected carry-forward and a structural '
      'poll-window Monte-Carlo. One row per (target_friday, as_of_date, model_version), '
      'upserted daily Sat..Thu as the horizon shrinks.')
    """).result()

    client.query(f"""
    CREATE OR REPLACE VIEW `{cfg.PROJECT}.{cfg.OUTPUT_CURRENT_VIEW}` AS
    SELECT * EXCEPT(rn) FROM (
      SELECT *, ROW_NUMBER() OVER (
                  PARTITION BY target_friday, model_version
                  ORDER BY as_of_date DESC, generated_at DESC) AS rn
      FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`
    ) WHERE rn = 1
    """).result()


def _ensure_releases_table_and_view(client: bigquery.Client) -> None:
    client.query(f"""
    CREATE TABLE IF NOT EXISTS `{cfg.PROJECT}.{cfg.OUTPUT_RELEASES_TABLE}` (
      target_friday     DATE      NOT NULL,
      as_of_date        DATE      NOT NULL,
      generated_at      TIMESTAMP NOT NULL,
      horizon_days      INT64,
      pollster          STRING    NOT NULL,
      release_prob      FLOAT64,
      expected_approve  FLOAT64,
      days_since_last   INT64,
      in_current_window BOOL,
      model_version     STRING,
      run_id            STRING
    )
    PARTITION BY target_friday
    OPTIONS (description = 'Per-pollster probability of releasing a poll on the upcoming '
      'Friday (and its modeled approve value if it does). One row per house with '
      'non-trivial probability, per (target_friday, as_of_date, model_version), '
      'upserted daily alongside the average forecast.')
    """).result()

    client.query(f"""
    CREATE OR REPLACE VIEW `{cfg.PROJECT}.{cfg.OUTPUT_RELEASES_CURRENT_VIEW}` AS
    WITH latest AS (
      SELECT target_friday, model_version, MAX(as_of_date) AS as_of_date
      FROM `{cfg.PROJECT}.{cfg.OUTPUT_RELEASES_TABLE}`
      GROUP BY target_friday, model_version
    )
    SELECT r.* FROM `{cfg.PROJECT}.{cfg.OUTPUT_RELEASES_TABLE}` r
    JOIN latest USING (target_friday, model_version, as_of_date)
    ORDER BY r.target_friday, r.release_prob DESC
    """).result()


def _upsert(client: bigquery.Client, fc: Forecast, generated_at: datetime, run_id: str) -> None:
    params = [
        bigquery.ScalarQueryParameter("target_friday", "DATE", fc.target_friday),
        bigquery.ScalarQueryParameter("as_of_date", "DATE", fc.as_of_date),
        bigquery.ScalarQueryParameter("model_version", "STRING", fc.model_version),
    ]
    client.query(
        f"""DELETE FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`
            WHERE target_friday = @target_friday
              AND as_of_date = @as_of_date
              AND model_version = @model_version""",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()
    row = {
        "target_friday": fc.target_friday.isoformat(),
        "as_of_date": fc.as_of_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "horizon_days": fc.horizon_days,
        "forecast": fc.forecast,
        "forecast_rounded": fc.forecast_rounded,
        "carry_forward": fc.carry_forward,
        "drift_corrected_carry": fc.drift_corrected_carry,
        "structural_mean": fc.structural_mean,
        "drift_per_day": fc.drift_per_day,
        "band_lo": fc.band_lo,
        "band_hi": fc.band_hi,
        "n_window": fc.n_window,
        "model_version": fc.model_version,
        "run_id": run_id,
    }
    client.load_table_from_json(
        [row],
        f"{cfg.PROJECT}.{cfg.OUTPUT_TABLE}",
        job_config=bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND),
    ).result()


def _upsert_releases(client: bigquery.Client, fc: Forecast, generated_at: datetime,
                     run_id: str) -> int:
    params = [
        bigquery.ScalarQueryParameter("target_friday", "DATE", fc.target_friday),
        bigquery.ScalarQueryParameter("as_of_date", "DATE", fc.as_of_date),
        bigquery.ScalarQueryParameter("model_version", "STRING", fc.model_version),
    ]
    client.query(
        f"""DELETE FROM `{cfg.PROJECT}.{cfg.OUTPUT_RELEASES_TABLE}`
            WHERE target_friday = @target_friday
              AND as_of_date = @as_of_date
              AND model_version = @model_version""",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()
    rows = [
        {
            "target_friday": fc.target_friday.isoformat(),
            "as_of_date": fc.as_of_date.isoformat(),
            "generated_at": generated_at.isoformat(),
            "horizon_days": fc.horizon_days,
            "pollster": r.pollster,
            "release_prob": r.release_prob,
            "expected_approve": r.expected_approve,
            "days_since_last": r.days_since_last,
            "in_current_window": r.in_current_window,
            "model_version": fc.model_version,
            "run_id": run_id,
        }
        for r in fc.friday_releases
        if r.release_prob >= cfg.RELEASE_PROB_FLOOR
    ]
    if rows:
        client.load_table_from_json(
            rows,
            f"{cfg.PROJECT}.{cfg.OUTPUT_RELEASES_TABLE}",
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND),
        ).result()
    return len(rows)


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    run_id = _run_id()
    log = configure_logging("forecast.rcp_approval", run_id)
    started = time.monotonic()

    settings = Settings.from_env()
    client = bigquery.Client(project=settings.project_id)

    today = datetime.now(TZ).date()
    target = _upcoming_friday(today)
    horizon = (target - today).days
    if horizon < 1 or horizon > 6:  # Friday: target realises today; new cycle starts Saturday
        log.info("idle: outside the Sat..Thu forecast window",
                 extra={"extras": {"today": today.isoformat(), "horizon": horizon}})
        return

    log.info("pulling snapshot history from BigQuery")
    windows, truth, releases = data.load_all(client)
    fc = model.forecast(windows, truth, releases, as_of=today, target_friday=target)
    if fc is None:
        log.warning("no forecast produced (no usable window before today); skipping",
                    extra={"extras": {"today": today.isoformat()}})
        return

    generated_at = datetime.now(UTC)
    audit = {
        "target_friday": fc.target_friday.isoformat(),
        "as_of_date": fc.as_of_date.isoformat(),
        "horizon_days": fc.horizon_days,
        "forecast": fc.forecast_rounded,
        "carry_forward": round(fc.carry_forward, 2),
        "structural_mean": round(fc.structural_mean, 2),
        "drift_per_day": round(fc.drift_per_day, 3),
        "band": [round(fc.band_lo, 2), round(fc.band_hi, 2)],
        "n_window": fc.n_window,
        "model_version": fc.model_version,
        "friday_releases": [
            {"pollster": r.pollster, "p": round(r.release_prob, 2)}
            for r in fc.friday_releases if r.release_prob >= cfg.RELEASE_PROB_FLOOR
        ],
    }
    log.info("forecast computed", extra={"extras": audit})

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        log.info("DRY_RUN: skipping table ensure + upsert",
                 extra={"extras": {"duration_s": round(time.monotonic() - started, 2), **audit}})
        return

    _ensure_table_and_view(client)
    _ensure_releases_table_and_view(client)
    _upsert(client, fc, generated_at, run_id)
    n_rel = _upsert_releases(client, fc, generated_at, run_id)
    log.info("forecast rows upserted",
             extra={"extras": {"table": cfg.OUTPUT_TABLE, "releases_table": cfg.OUTPUT_RELEASES_TABLE,
                               "n_releases": n_rel,
                               "duration_s": round(time.monotonic() - started, 2), **audit}})


if __name__ == "__main__":
    main()
