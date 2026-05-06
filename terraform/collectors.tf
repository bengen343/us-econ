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

module "claims" {
  source = "./modules/cloud_run_job"

  name       = "claims"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.claims"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # DOL unemployment insurance weekly claims press release drops every Thursday
  # at 08:30 ET (06:30 MT). Cron fires Thursdays at 06:45 MT to give the PDF
  # time to land. The doleta.gov XML archive updates the same morning; the
  # press PDF is generally 1-4 weeks ahead of it, so both feeds are pulled per run.
  schedule          = "45 6 * * 4"
  schedule_timezone = "America/Denver"
  timeout           = "600s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.claims,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_claims_editor,
  ]
}
