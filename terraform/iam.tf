# Single service account for everything this project automates:
#   - the Cloud Run Jobs run as it (BigQuery, GCS, Secret Manager access)
#   - Cloud Scheduler invokes the Jobs as it (run.invoker, granted per-Job in the module)

resource "google_service_account" "runner" {
  account_id   = "runner"
  display_name = "us-econ runner"
  description  = "Identity for all automated us-econ workloads (Cloud Run Jobs and Cloud Scheduler invocations)."

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "runner_raw_writer" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_bls_editor" {
  dataset_id = google_bigquery_dataset.bls.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_claims_editor" {
  dataset_id = google_bigquery_dataset.claims.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_adp_employment_editor" {
  dataset_id = google_bigquery_dataset.adp_employment.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_challenger_employment_editor" {
  dataset_id = google_bigquery_dataset.challenger_employment.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_aaa_gasoline_editor" {
  dataset_id = google_bigquery_dataset.aaa_gasoline.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_google_trends_viewer" {
  dataset_id = google_bigquery_dataset.google_trends.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_conference_board_editor" {
  dataset_id = google_bigquery_dataset.conference_board.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_ism_editor" {
  dataset_id = google_bigquery_dataset.ism.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_census_construction_editor" {
  dataset_id = google_bigquery_dataset.census_construction.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_nahb_hmi_editor" {
  dataset_id = google_bigquery_dataset.nahb_hmi.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_market_indexes_editor" {
  dataset_id = google_bigquery_dataset.market_indexes.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_michigan_sentiment_editor" {
  dataset_id = google_bigquery_dataset.michigan_sentiment.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_bls_cpi_editor" {
  dataset_id = google_bigquery_dataset.bls_cpi.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_bls_ppi_editor" {
  dataset_id = google_bigquery_dataset.bls_ppi.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_eia_petroleum_editor" {
  dataset_id = google_bigquery_dataset.eia_petroleum.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_zillow_rent_editor" {
  dataset_id = google_bigquery_dataset.zillow_rent.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_bls_ntr_editor" {
  dataset_id = google_bigquery_dataset.bls_ntr.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_energy_futures_editor" {
  dataset_id = google_bigquery_dataset.energy_futures.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_manheim_used_vehicles_editor" {
  dataset_id = google_bigquery_dataset.manheim_used_vehicles.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

# Build-time roles. The runner SA also runs Cloud Build, so it needs to push
# images to Artifact Registry and stream build logs to Cloud Logging.

resource "google_project_iam_member" "runner_artifactregistry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_logging_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runner.email}"
}
