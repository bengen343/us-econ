locals {
  required_apis = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each                   = toset(local.required_apis)
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false
}
