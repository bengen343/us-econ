# Datasets only. Tables are created on first load by the collectors via
# CREATE_IF_NEEDED, so the Python schema stays the single source of truth.

resource "google_bigquery_dataset" "bls" {
  dataset_id  = "bls_employment"
  description = "BLS monthly payrolls."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "claims" {
  dataset_id  = "claims"
  description = "DOL weekly unemployment insurance claims (national + state, full revision history)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}
