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

resource "google_bigquery_dataset" "adp_employment" {
  dataset_id  = "adp_employment"
  description = "ADP National Employment Report: monthly NER history (CSV) + weekly preliminary estimates (NER Pulse)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "challenger_employment" {
  dataset_id  = "challenger_employment"
  description = "Challenger, Gray & Christmas monthly job cut announcement report (parsed from PDF)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "aaa_gasoline" {
  dataset_id  = "aaa_gasoline"
  description = "AAA national average retail gasoline prices, scraped daily from gasprices.aaa.com."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}
