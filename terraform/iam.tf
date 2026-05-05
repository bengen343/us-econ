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
