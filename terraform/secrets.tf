# Secret Manager entries for collector credentials. Versions (the actual values)
# are added out-of-band so they aren't stored in Terraform state.

resource "google_secret_manager_secret" "bls_api_key" {
  secret_id = "bls-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "runner_bls_key_accessor" {
  secret_id = google_secret_manager_secret.bls_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_secret_manager_secret" "bea_api_key" {
  secret_id = "bea-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "runner_bea_key_accessor" {
  secret_id = google_secret_manager_secret.bea_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_secret_manager_secret" "eia_api_key" {
  secret_id = "eia-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "runner_eia_key_accessor" {
  secret_id = google_secret_manager_secret.eia_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runner.email}"
}
