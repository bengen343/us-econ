"""Cloud Run Job: TimesFM 3.0 probability distribution over 5k claims buckets.

The target is the *advance* (first-print) SA initial-claims headline, which is
what Kalshi's KXJOBLESSCLAIMS contracts settle on.

Flow:
  1. AI.FORECAST (TimesFM 3.0, zero-shot) over claims.fct_sa_input -> point
     forecast + 95% prediction interval, h=1..4.
  2. Quantile-match a two-piece normal to (lower, point, upper).
  3. Integrate it over the 5,000-wide strike grid, with a half-rounding-unit
     continuity correction, to get both P(>= strike) and P(in bucket).
  4. Append one row per (horizon, strike) to claims.forecast_claims_buckets.

Why this job runs its own AI.FORECAST instead of reading the level table: the
suspended direction job failed in exactly that shape -- it depended on a
separate BigQuery Scheduled Query having already written the generation it
wanted to update. Self-contained is one fewer ordering dependency.

Set DRY_RUN=1 to compute and log without writing.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, date, datetime

from google.cloud import bigquery

from collectors.common.config import Settings
from collectors.common.logging import configure_logging
from forecasts.claims.initial_claims.bucket_probs import config as cfg
from forecasts.claims.initial_claims.bucket_probs.distribution import SplitNormal, bucket_table


def _ensure_table_and_view(client: bigquery.Client) -> None:
    client.query(f"""
    CREATE TABLE IF NOT EXISTS `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}` (
      generated_at     TIMESTAMP NOT NULL,
      data_through     DATE      NOT NULL,
      horizon          INT64     NOT NULL,
      target_week      DATE      NOT NULL,
      model_version    STRING    NOT NULL,
      point_sa         FLOAT64,
      lower_sa         FLOAT64,
      upper_sa         FLOAT64,
      confidence_level FLOAT64,
      sigma_lo         FLOAT64,
      sigma_hi         FLOAT64,
      bucket_low       INT64     NOT NULL,
      bucket_high      INT64     NOT NULL,
      p_at_least       FLOAT64,
      p_bucket         FLOAT64,
      run_id           STRING
    )
    PARTITION BY data_through
    OPTIONS (description =
      'Probability distribution over 5k initial-claims buckets from TimesFM 3.0. '
      'p_at_least is P(advance SA headline >= bucket_low) and maps 1:1 to a Kalshi '
      'KXJOBLESSCLAIMS "At least N" strike (join on bucket_low = floor_strike); '
      'p_bucket is P(bucket_low <= headline < bucket_high). Append-only, one '
      'generation per weekly run.')
    """).result()

    client.query(f"""
    CREATE OR REPLACE VIEW `{cfg.PROJECT}.{cfg.OUTPUT_CURRENT_VIEW}` AS
    SELECT * FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`
    WHERE generated_at = (SELECT MAX(generated_at)
                          FROM `{cfg.PROJECT}.{cfg.OUTPUT_TABLE}`)
    """).result()


def fetch_forecast(client: bigquery.Client) -> list[dict]:
    """TimesFM 3.0 point + interval per horizon, plus the origin week."""
    sql = f"""
    WITH hist AS (
      SELECT TIMESTAMP(week_ending) AS ts, value AS sa
      FROM `{cfg.PROJECT}.claims.fct_sa_input`
      WHERE week_ending >= DATE '{cfg.TRAIN_FLOOR}'
        AND week_ending <= CURRENT_DATE()
    )
    SELECT
      (SELECT MAX(DATE(ts)) FROM hist)        AS data_through,
      DATE(forecast_timestamp)                AS target_week,
      forecast_value                          AS point_sa,
      prediction_interval_lower_bound         AS lower_sa,
      prediction_interval_upper_bound         AS upper_sa
    FROM AI.FORECAST(
      TABLE hist,
      data_col => 'sa', timestamp_col => 'ts',
      model => '{cfg.TIMESFM_MODEL}',
      horizon => {cfg.HORIZONS},
      confidence_level => {cfg.CONFIDENCE_LEVEL})
    ORDER BY target_week
    """
    return [dict(r) for r in client.query(sql).result()]


def build_rows(forecasts: list[dict], generated_at: datetime, run_id: str) -> list[dict]:
    if not forecasts:
        raise RuntimeError("AI.FORECAST returned no rows")

    origin = forecasts[0]["data_through"]
    age = (date.today() - origin).days
    if age > cfg.MAX_ORIGIN_AGE_DAYS:
        raise RuntimeError(
            f"stale origin: claims.fct_sa_input ends {origin} ({age} days old, "
            f"limit {cfg.MAX_ORIGIN_AGE_DAYS}). The claims collector has probably "
            f"failed -- refusing to publish a forecast from stale data."
        )

    rows: list[dict] = []
    for f in forecasts:
        dist = SplitNormal.from_interval(
            f["point_sa"], f["lower_sa"], f["upper_sa"], cfg.CONFIDENCE_LEVEL
        )
        horizon = (f["target_week"] - origin).days // 7
        for b in bucket_table(
            dist,
            bucket_width=cfg.BUCKET_WIDTH,
            rounding_unit=cfg.ROUNDING_UNIT,
            n_each_side=cfg.N_STRIKES_EACH_SIDE,
        ):
            rows.append({
                "generated_at": generated_at.isoformat(),
                "data_through": origin.isoformat(),
                "horizon": horizon,
                "target_week": f["target_week"].isoformat(),
                "model_version": cfg.MODEL_VERSION,
                "point_sa": float(f["point_sa"]),
                "lower_sa": float(f["lower_sa"]),
                "upper_sa": float(f["upper_sa"]),
                "confidence_level": cfg.CONFIDENCE_LEVEL,
                "sigma_lo": dist.sigma_lo,
                "sigma_hi": dist.sigma_hi,
                "bucket_low": int(b["bucket_low"]),
                "bucket_high": int(b["bucket_high"]),
                "p_at_least": b["p_at_least"],
                "p_bucket": b["p_bucket"],
                "run_id": run_id,
            })
    return rows


def main() -> None:
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    log = configure_logging("forecast.claims_bucket_probs", run_id)
    started = time.monotonic()

    settings = Settings.from_env()
    if settings.project_id != cfg.PROJECT:
        log.warning("project mismatch", extra={"extras": {
            "settings_project": settings.project_id, "code_project": cfg.PROJECT}})
    client = bigquery.Client(project=settings.project_id)

    generated_at = datetime.now(UTC)
    forecasts = fetch_forecast(client)
    rows = build_rows(forecasts, generated_at, run_id)

    h1 = [r for r in rows if r["horizon"] == 1]
    modal = max(h1, key=lambda r: r["p_bucket"])
    log.info("forecast built", extra={"extras": {
        "data_through": rows[0]["data_through"],
        "h1_target_week": h1[0]["target_week"],
        "h1_point": round(h1[0]["point_sa"]),
        "h1_modal_bucket": f"{modal['bucket_low']}-{modal['bucket_high']}",
        "h1_modal_p": round(modal["p_bucket"], 4),
        "n_rows": len(rows),
    }})

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        for r in sorted(h1, key=lambda x: x["bucket_low"]):
            log.info("h1 bucket", extra={"extras": {
                "bucket": f"{r['bucket_low']}-{r['bucket_high']}",
                "p_at_least": round(r["p_at_least"], 4),
                "p_bucket": round(r["p_bucket"], 4)}})
        log.info("DRY_RUN: skipping write")
        return

    _ensure_table_and_view(client)
    client.load_table_from_json(
        rows,
        f"{cfg.PROJECT}.{cfg.OUTPUT_TABLE}",
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND
        ),
    ).result()

    log.info("rows written", extra={"extras": {
        "n_rows": len(rows), "duration_s": round(time.monotonic() - started, 2)}})
