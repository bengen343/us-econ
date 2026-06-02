# Python-based forecasts run as Cloud Run Jobs in the same container image
# as the collectors. The existing SQL-based forecasts (the ARIMA+TimesFM
# stored procedure called by a BigQuery Scheduled Query) are unaffected by
# this — these Python jobs run *after* them and UPDATE the same generation's
# h=1 row with direction-prediction columns.

module "direction_lgbm_initial_claims" {
  source = "./modules/cloud_run_job"

  name       = "forecast-direction-initial-claims"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.claims.initial_claims.direction_lgbm"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Initial claims press release drops Thursday 08:30 ET. The existing
  # forecast_sa_initial_claims Scheduled Query runs sometime before this. We
  # fire at 17:30 UTC Thursdays — a safe buffer after both the data release
  # and the level-forecast scheduled query, before US-Pacific business hours
  # consumers wake up.
  schedule          = "30 17 * * 4"
  schedule_timezone = "Etc/UTC"

  # LGBM walk-forward calibration loop refits the model ~26 times for the
  # calibration history (one fit per origin in the prior 26 weeks) plus the
  # final fit. ~10s per fit on our data => 4-5 min worst case.
  timeout = "600s"
  memory  = "1Gi"
  cpu     = "1"

  depends_on = [
    google_bigquery_dataset.claims,
    google_bigquery_dataset.google_trends,
    google_bigquery_dataset.adp_employment,
    google_bigquery_dataset_iam_member.runner_claims_editor,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
    google_bigquery_dataset_iam_member.runner_google_trends_viewer,
  ]
}

module "adp_headline_pulse_bridge" {
  source = "./modules/cloud_run_job"

  name       = "forecast-adp-headline-pulse"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.adp_employment.national_monthly.pulse_bridge"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # The NER Pulse preliminary estimate is collected Tuesdays at 06:30 MT
  # (module "adp_employment_weekly"). Fire at 07:30 MT Tuesdays so the freshest
  # Pulse vintage has landed; the job re-nowcasts the next unreleased monthly
  # headline and upserts a revision row. Runs are cheap + idempotent, so weeks
  # with no new Pulse vintage simply rewrite the same row with a new timestamp.
  # The job also self-bootstraps its output table + _current view on first run.
  schedule          = "30 7 * * 2"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.adp_employment,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
  ]
}

module "employment_situation_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-employment-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_employment.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released Employment Situation (NFP MoM change +
  # unemployment rate) from a small model ensemble. The release is the first
  # Friday at 08:30 ET (06:30 MT); the BLS collector loads it at 07:30 MT. We
  # fire every day in the first week at 05:00 MT — before the release — and the
  # job gates in code to weekdays on/before the first Friday, re-running each day
  # so the forecast firms up as Conference Board / ISM / ADP / claims inputs land.
  # The DFM refits each run (~20s). Self-bootstraps its table + _current view.
  schedule          = "0 5 1-7 * *"
  schedule_timezone = "America/Denver"
  timeout           = "600s"
  memory            = "1Gi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls,
    google_bigquery_dataset.claims,
    google_bigquery_dataset.adp_employment,
    google_bigquery_dataset.conference_board,
    google_bigquery_dataset.ism,
    google_bigquery_dataset.google_trends,
    google_bigquery_dataset_iam_member.runner_bls_editor,
  ]
}
