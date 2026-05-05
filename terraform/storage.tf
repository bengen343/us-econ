locals {
  raw_bucket_name = coalesce(var.raw_bucket_name, "${var.project_id}-raw")
}

resource "google_storage_bucket" "raw" {
  name                        = local.raw_bucket_name
  location                    = var.bq_location
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = false
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 1095
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  depends_on = [google_project_service.enabled]
}
