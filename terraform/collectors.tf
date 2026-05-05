locals {
  collector_env = {
    GCP_PROJECT = var.project_id
    RAW_BUCKET  = google_storage_bucket.raw.name
    BQ_LOCATION = var.bq_location
  }
}

module "bls_employment" {
  source = "./modules/cloud_run_job"

  name       = "bls-employment"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.bls_employment"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Employment Situation drops first Friday of each month at 08:30 ET (06:30 MT).
  # Cron fires every Friday at 07:30 MT; the collector itself skips Fridays past
  # day 7 of the month. (Unix-cron can't express "first Friday" — restricting both
  # day-of-month and day-of-week is OR'd, not AND'd, so we filter in code.)
  schedule          = "30 7 * * 5"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_bls_editor,
    google_secret_manager_secret_iam_member.runner_bls_key_accessor,
  ]
}
