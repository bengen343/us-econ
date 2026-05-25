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

module "adp_employment_weekly" {
  source = "./modules/cloud_run_job"

  name       = "adp-employment-weekly"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.adp_employment.weekly"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # ADP NER Pulse preliminary estimate releases every Tuesday morning. Cron fires
  # Tuesdays at 06:30 MT.
  schedule          = "30 6 * * 2"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.adp_employment,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
  ]
}

module "adp_employment_monthly" {
  source = "./modules/cloud_run_job"

  name       = "adp-employment-monthly"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.adp_employment.monthly"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # ADP NER monthly report drops the first Wednesday of each month. Cron fires
  # every Wednesday at 06:30 MT; the collector itself bails on Wednesdays past
  # day 7 of the month. (Same first-of-month gating pattern as bls_employment.)
  schedule          = "30 6 * * 3"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.adp_employment,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
  ]
}

module "aaa_gasoline" {
  source = "./modules/cloud_run_job"

  name       = "aaa-gasoline"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.aaa_gasoline"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # AAA refreshes the National Average Gas Prices table daily. Cron fires every
  # day at 06:00 MT.
  schedule          = "0 6 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.aaa_gasoline,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_aaa_gasoline_editor,
  ]
}

module "challenger_employment" {
  source = "./modules/cloud_run_job"

  name       = "challenger-employment"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.challenger_employment"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Challenger Job Cut Announcement Report drops the first Thursday of each
  # month at 08:30 MT (per the user; the PDF itself is embargoed to 07:30 ET).
  # Cron fires every Thursday at 08:30 MT; the collector bails on Thursdays
  # past day 7 of the month.
  schedule          = "30 8 * * 4"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.challenger_employment,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_challenger_employment_editor,
  ]
}

