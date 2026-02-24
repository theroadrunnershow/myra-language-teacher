# Billing budget + Pub/Sub + Cloud Functions kill-switch
# Mirrors the AWS Budgets + SNS + Lambda pattern:
#   $40 (80%) → email warning
#   $50 (100%) → email + Cloud Function scales Cloud Run to 0

# ── Pub/Sub topic for budget alerts ──────────────────────────────────────────
resource "google_pubsub_topic" "budget_alerts" {
  name = "dino-app-budget-alerts"
}

# ── Cloud Function kill-switch ────────────────────────────────────────────────
data "archive_file" "kill_run" {
  type        = "zip"
  source_file = "${path.module}/lambda/kill_run.py"
  output_path = "${path.module}/lambda/kill_run.zip"
}

resource "google_storage_bucket" "functions" {
  name                        = "${var.project_id}-functions"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

resource "google_storage_bucket_object" "kill_run" {
  name   = "kill_run_${data.archive_file.kill_run.output_md5}.zip"
  bucket = google_storage_bucket.functions.name
  source = data.archive_file.kill_run.output_path
}

resource "google_cloudfunctions2_function" "kill_run" {
  name     = "dino-app-kill-run"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "handler"

    source {
      storage_source {
        bucket = google_storage_bucket.functions.name
        object = google_storage_bucket_object.kill_run.name
      }
    }
  }

  service_config {
    min_instance_count    = 0
    max_instance_count    = 1
    available_memory      = "256M"
    timeout_seconds       = 30
    service_account_email = google_service_account.kill_run.email

    environment_variables = {
      CLOUD_RUN_SERVICE = google_cloud_run_v2_service.app.name
      CLOUD_RUN_REGION  = var.region
      GCP_PROJECT       = var.project_id
    }
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = google_pubsub_topic.budget_alerts.id
    retry_policy   = "RETRY_POLICY_RETRY"
  }

  depends_on = [
    google_project_service.eventarc,
    google_project_service.cloudfunctions,
  ]
}

resource "google_service_account" "kill_run" {
  account_id   = "dino-app-kill-run"
  display_name = "Dino App Kill-Switch Function"
}

resource "google_project_iam_member" "kill_run_cloud_run" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.kill_run.email}"
}

resource "google_project_iam_member" "kill_run_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.kill_run.email}"
}

# ── Billing budget ─────────────────────────────────────────────────────────────
resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "dino-app-monthly-budget"

  budget_filter {
    projects = ["projects/${data.google_project.project.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_limit)
    }
  }

  # 80% ($40) — email warning only
  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }

  # 100% ($50) — triggers Pub/Sub → Cloud Function kill-switch
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    pubsub_topic                     = google_pubsub_topic.budget_alerts.id
    schema_version                   = "1.0"
    monitoring_notification_channels = []
    disable_default_iam_recipients   = false
  }
}
