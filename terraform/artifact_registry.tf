resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = "us-econ"
  description   = "Container images for us-econ collector jobs."
  format        = "DOCKER"

  depends_on = [google_project_service.enabled]
}

locals {
  image_repo = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.containers.repository_id}"
  image_uri  = "${local.image_repo}/collector:${var.image_tag}"
}
