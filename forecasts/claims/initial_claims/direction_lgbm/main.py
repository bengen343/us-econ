"""Cloud Run Job entrypoint for the SA initial-claims direction forecast.

Flow:
  1. Pull SA, Trends, ADP data from BigQuery.
  2. Build the panel and resolve the freshest valid ADP lag for today's origin.
  3. Train LGBM + isotonic calibration walk-forward.
  4. Look up the latest existing generation in forecast_sa_initial_claims for
     the same data_through and horizon=1.
  5. UPDATE that row's direction columns. Idempotent (re-running overwrites).

Fails loudly (non-zero exit, structured log) on any precondition violation —
the scheduled query is the source of truth for level forecasts, so if it
hasn't run yet, this job should not silently create a partial row.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import UTC, datetime

import pandas as pd
from google.cloud import bigquery

from collectors.common.config import Settings
from collectors.common.logging import configure_logging
from forecasts.claims.initial_claims.direction_lgbm.features import (
    PanelInputs,
    build_panel,
    resolve_adp_diff_lag,
)
from forecasts.claims.initial_claims.direction_lgbm.model import fit_predict_and_calibrate
from forecasts.claims.initial_claims.direction_lgbm.series import (
    FEATURE_SET_NAME,
    OUTPUT_TABLE,
    PROJECT,
)


def _pull_sa(client: bigquery.Client) -> pd.DataFrame:
    sql = f"""
    SELECT
      i.week_ending,
      i.value          AS sa_input,
      a.sa_as_reported AS sa_actual
    FROM `{PROJECT}.claims.fct_sa_input` i
    LEFT JOIN `{PROJECT}.claims.fct_actuals_as_reported` a USING (week_ending)
    ORDER BY i.week_ending
    """
    df = client.query(sql).to_dataframe()
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    return df


def _pull_trends(client: bigquery.Client) -> pd.DataFrame:
    sql = f"""
    WITH ranked AS (
      SELECT week_ending, series_id, value,
             ROW_NUMBER() OVER (PARTITION BY series_id, week_ending
                                  ORDER BY vintage_date DESC, is_partial ASC) AS rn
      FROM `{PROJECT}.google_trends.weekly`
    )
    SELECT week_ending, series_id, value FROM ranked WHERE rn = 1
    """
    long = client.query(sql).to_dataframe()
    long["week_ending"] = pd.to_datetime(long["week_ending"])
    wide = long.pivot(index="week_ending", columns="series_id", values="value")
    wide.columns = [c.replace("trends.us.", "trends_") for c in wide.columns]
    return wide.reset_index().sort_values("week_ending")


def _pull_adp(client: bigquery.Client) -> pd.DataFrame:
    sql = f"""
    SELECT observation_date AS week_ending, ner
    FROM `{PROJECT}.adp_employment.ner_history`
    WHERE timestep = 'W' AND aggregation = 'National' AND category = 'U.S.'
    ORDER BY observation_date
    """
    df = client.query(sql).to_dataframe()
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    return df


def _find_target_row(client: bigquery.Client, data_through: pd.Timestamp) -> dict | None:
    """Find the latest h=1 row in forecast_sa_initial_claims_current for the
    given data_through. Returns the row dict or None."""
    sql = f"""
    SELECT generated_at, data_through, horizon, target_week
    FROM `{PROJECT}.claims.forecast_sa_initial_claims_current`
    WHERE horizon = 1
      AND data_through = DATE '{data_through.date().isoformat()}'
    LIMIT 1
    """
    rows = list(client.query(sql).result())
    if not rows:
        return None
    r = rows[0]
    return {
        "generated_at": r.generated_at,
        "data_through": r.data_through,
        "horizon": int(r.horizon),
        "target_week": r.target_week,
    }


def _update_direction_row(
    client: bigquery.Client,
    generated_at: datetime,
    data_through,
    horizon: int,
    *,
    pred_dir_up: bool,
    p_up_raw: float,
    p_up_calibrated: float,
    feature_set: str,
    n_train_origins: int,
) -> None:
    """UPDATE the h=1 row of the latest existing generation with direction
    outputs. The (generated_at, horizon, data_through) tuple uniquely
    identifies the row in the table (data_through is the partition key)."""
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("generated_at", "TIMESTAMP", generated_at),
            bigquery.ScalarQueryParameter("horizon", "INT64", horizon),
            bigquery.ScalarQueryParameter("data_through", "DATE", data_through),
            bigquery.ScalarQueryParameter("pred_dir_up", "BOOL", pred_dir_up),
            bigquery.ScalarQueryParameter("p_up_raw", "FLOAT64", p_up_raw),
            bigquery.ScalarQueryParameter("p_up_calibrated", "FLOAT64", p_up_calibrated),
            bigquery.ScalarQueryParameter("feature_set", "STRING", feature_set),
            bigquery.ScalarQueryParameter("n_train_origins", "INT64", n_train_origins),
        ]
    )
    sql = f"""
    UPDATE `{PROJECT}.{OUTPUT_TABLE}`
       SET pred_dir_up         = @pred_dir_up,
           p_up_raw            = @p_up_raw,
           p_up_calibrated     = @p_up_calibrated,
           dir_feature_set     = @feature_set,
           dir_n_train_origins = @n_train_origins
     WHERE generated_at = @generated_at
       AND horizon      = @horizon
       AND data_through = @data_through
    """
    job = client.query(sql, job_config=job_config)
    job.result()


def main() -> None:
    log = configure_logging("forecast.direction_lgbm", _run_id())
    started = time.monotonic()

    settings = Settings.from_env()
    if settings.project_id != PROJECT:
        log.warning("project mismatch", extra={"extras": {
            "settings_project": settings.project_id, "code_project": PROJECT}})

    client = bigquery.Client(project=settings.project_id)

    log.info("pulling inputs from BigQuery")
    sa = _pull_sa(client)
    trends = _pull_trends(client)
    adp = _pull_adp(client)
    log.info("inputs loaded", extra={"extras": {
        "sa_rows": len(sa), "sa_last_week": sa["week_ending"].max().date().isoformat(),
        "trends_rows": len(trends), "trends_last_week": trends["week_ending"].max().date().isoformat(),
        "adp_rows": len(adp), "adp_last_week": adp["week_ending"].max().date().isoformat(),
    }})

    inputs = PanelInputs(sa=sa, trends=trends, adp=adp)
    latest_origin = sa["week_ending"].max()

    adp_lag = resolve_adp_diff_lag(inputs, latest_origin)
    if adp_lag != 8:
        log.warning("ADP lag-8 unavailable; falling back",
                     extra={"extras": {"resolved_adp_lag": adp_lag}})

    panel, feature_cols = build_panel(inputs, adp_diff_lag=adp_lag)
    log.info("panel built",
              extra={"extras": {"n_rows": len(panel), "n_features": len(feature_cols),
                                  "adp_lag": adp_lag}})

    # Locate the level-forecast row to update.
    target_row = _find_target_row(client, latest_origin)
    if target_row is None:
        log.error(
            "no level-forecast row found in forecast_sa_initial_claims_current for "
            f"data_through={latest_origin.date()}; ensure the existing "
            "claims.fct_forecast_sa_initial_claims() proc has run for this week",
            extra={"extras": {"data_through": latest_origin.date().isoformat()}},
        )
        sys.exit(2)
    log.info("found target row",
              extra={"extras": {
                  "generated_at": target_row["generated_at"].isoformat(),
                  "data_through": target_row["data_through"].isoformat(),
                  "target_week": target_row["target_week"].isoformat(),
              }})

    # Train + predict.
    result = fit_predict_and_calibrate(panel, feature_cols, latest_origin)
    log.info("model trained, prediction ready", extra={"extras": result})

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        log.info("DRY_RUN: skipping UPDATE step", extra={"extras": {
            "would_update_generated_at": target_row["generated_at"].isoformat(),
            "would_update_horizon": target_row["horizon"],
            "pred_dir_up": bool(result["pred_dir_up"]),
            "p_up_raw": round(result["p_up_raw"], 4),
            "p_up_calibrated": round(result["p_up_calibrated"], 4),
            "duration_s": round(time.monotonic() - started, 2),
        }})
        return

    # UPDATE the h=1 row.
    _update_direction_row(
        client,
        generated_at=target_row["generated_at"],
        data_through=target_row["data_through"],
        horizon=target_row["horizon"],
        pred_dir_up=bool(result["pred_dir_up"]),
        p_up_raw=result["p_up_raw"],
        p_up_calibrated=result["p_up_calibrated"],
        feature_set=FEATURE_SET_NAME,
        n_train_origins=result["n_train_origins"],
    )

    log.info("direction row updated", extra={"extras": {
        "data_through": target_row["data_through"].isoformat(),
        "target_week": target_row["target_week"].isoformat(),
        "pred_dir_up": bool(result["pred_dir_up"]),
        "p_up_raw": round(result["p_up_raw"], 4),
        "p_up_calibrated": round(result["p_up_calibrated"], 4),
        "duration_s": round(time.monotonic() - started, 2),
    }})


def _run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


if __name__ == "__main__":
    main()
