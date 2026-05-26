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
