# GitHub-driven trigger that builds and pushes the collector image whenever
# code under collectors/ (or build-relevant files) lands on main.
#
# REQUIRES one-time manual setup before this can apply:
#   1. Push this repo to GitHub.
#   2. Install the Google Cloud Build GitHub App on the repo:
#        https://github.com/marketplace/google-cloud-build
#   3. In GCP Console: Cloud Build → Triggers → Manage repositories →
#      Connect repository → "GitHub (Cloud Build GitHub App)" → select repo.
# After step 3 the legacy `github` block below can resolve.

resource "google_cloudbuild_trigger" "build_collector" {
  name        = "build-collector-on-push"
  description = "Build and push the collector container when collectors/, Dockerfile, pyproject.toml, or uv.lock changes on main."
  location    = "global"

  service_account = google_service_account.runner.id

  github {
    owner = var.github_owner
    name  = var.github_repo
    push {
      branch = "^main$"
    }
  }

  included_files = [
    "collectors/**",
    "Dockerfile",
    "pyproject.toml",
    "uv.lock",
    "cloudbuild.yaml",
  ]

  filename = "cloudbuild.yaml"

  substitutions = {
    _IMAGE_REPO = local.image_repo
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.runner_artifactregistry_writer,
    google_project_iam_member.runner_logging_writer,
    google_artifact_registry_repository.containers,
  ]
}
