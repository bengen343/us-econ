resource "google_cloud_run_v2_job" "this" {
  name                = var.name
  location            = var.region
  project             = var.project_id
  deletion_protection = false

  template {
    template {
      service_account = var.service_account_email
      timeout         = var.timeout
      max_retries     = var.max_retries

      containers {
        image   = var.image
        command = ["python", "-m"]
        args    = var.args

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        dynamic "env" {
          for_each = var.env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      # The image tag is rolled out by deploy scripts/CI, not by routine TF applies.
      template[0].template[0].containers[0].image,
    ]
  }
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_account_email}"
}

resource "google_cloud_scheduler_job" "this" {
  name             = var.name
  project          = var.project_id
  region           = var.region
  schedule         = var.schedule
  time_zone        = var.schedule_timezone
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.this.name}:run"

    oauth_token {
      service_account_email = var.service_account_email
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoker]
}
