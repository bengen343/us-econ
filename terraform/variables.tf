variable "project_id" {
  description = "GCP project ID hosting the us-econ pipeline."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Scheduler, Artifact Registry."
  type        = string
  default     = "us-west1"
}

variable "bq_location" {
  description = "BigQuery location for datasets. US multi-region is the cheapest default."
  type        = string
  default     = "US"
}

variable "image_tag" {
  description = "Container image tag the Cloud Run Job pulls. Cloud Build pushes :latest plus :<SHORT_SHA> on every build."
  type        = string
  default     = "latest"
}

variable "github_owner" {
  description = "GitHub user/org owning the source repository."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
}

variable "raw_bucket_name" {
  description = "Override for the raw archive bucket name. Defaults to <project>-raw."
  type        = string
  default     = null
}
