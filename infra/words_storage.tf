# Durable object storage for dynamically translated words.
# S3-equivalent pattern on GCS: single JSON object + object versioning enabled.

resource "google_storage_bucket" "words" {
  name                        = "${var.project_id}-dynamic-words"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  # Keep version history for recovery while limiting long-term storage costs.
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age                = var.words_noncurrent_age_days
      with_state         = "ARCHIVED"
      num_newer_versions = 10
    }
  }

  depends_on = [google_project_service.storage]
}

resource "google_storage_bucket_iam_member" "cloud_run_words_rw" {
  bucket = google_storage_bucket.words.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_compute_default_service_account.default.email}"
}
