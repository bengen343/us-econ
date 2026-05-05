output "image_repo" {
  description = "Push container images here (use this prefix in 'docker tag')."
  value       = local.image_repo
}

output "image_uri" {
  description = "Full image URI consumed by the Cloud Run Jobs."
  value       = local.image_uri
}

output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "runner_sa" {
  value = google_service_account.runner.email
}
