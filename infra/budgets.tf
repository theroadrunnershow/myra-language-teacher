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
  output_path = "${path.module}/lambda/kill_run.zip"
  # Cloud Functions Gen2 requires main.py — set filename inside zip accordingly
  source {
    content  = file("${path.module}/lambda/kill_run.py")
    filename = "main.py"
  }
  source {
    content  = file("${path.module}/lambda/requirements.txt")
    filename = "requirements.txt"
  }
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

# Scoped to this specific Cloud Run service only (not project-wide).
# Removes the ability to create/delete/modify any other Cloud Run service in the project.
resource "google_cloud_run_v2_service_iam_member" "kill_run_admin" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.admin"
  member   = "serviceAccount:${google_service_account.kill_run.email}"
}

resource "google_project_iam_member" "kill_run_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.kill_run.email}"
}

# ── Project data (needed for Cloud Monitoring service-agent IAM) ───────────────
data "google_project" "project" {
  project_id = var.project_id
  depends_on = [google_project_service.cloudresourcemanager]
}

# ── Daily cost guardrail: $20/day via Cloud Monitoring ────────────────────────
# GCP billing budgets only support MONTH/QUARTER/YEAR periods. The daily guardrail
# is implemented via a Cloud Monitoring alerting policy that computes the 24-hour
# delta of billing/monthly_cost and fires when it exceeds var.daily_budget_limit.

resource "google_pubsub_topic" "daily_alerts" {
  name = "dino-app-daily-alerts"
}

# Cloud Monitoring service agent must be able to publish to the topic
resource "google_pubsub_topic_iam_member" "monitoring_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.daily_alerts.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
}

resource "google_monitoring_notification_channel" "daily_pubsub" {
  display_name = "Daily Cost Alert → Pub/Sub"
  type         = "pubsub"

  labels = {
    topic = google_pubsub_topic.daily_alerts.id
  }

  depends_on = [
    google_project_service.monitoring,
    google_pubsub_topic_iam_member.monitoring_publisher,
  ]
}

# Alert fires when 24-hour billing cost delta > daily_budget_limit ($20 default)
resource "google_monitoring_alert_policy" "daily_cost" {
  count = 0  # billing metric not available until project has spend data
  display_name = "dino-app-daily-cost-guardrail"
  combiner     = "OR"

  conditions {
    display_name = "Daily billing spend > $${var.daily_budget_limit}"

    condition_threshold {
      filter = "metric.type=\"billing.googleapis.com/billing/monthly_cost\" resource.type=\"global\""

      aggregations {
        alignment_period     = "86400s"        # 24-hour window
        per_series_aligner   = "ALIGN_DELTA"   # cost increase over the window = daily spend
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = []
      }

      comparison      = "COMPARISON_GT"
      threshold_value = var.daily_budget_limit
      duration        = "0s"
    }
  }

  notification_channels = [google_monitoring_notification_channel.daily_pubsub.name]

  alert_strategy {
    auto_close = "86400s" # auto-resolve after 24h (metric resets with new day)
  }

  depends_on = [google_project_service.monitoring]
}

# Daily guardrail Cloud Function — kills Cloud Run when daily limit is breached
data "archive_file" "daily_guardrail" {
  type        = "zip"
  output_path = "${path.module}/lambda/daily_guardrail.zip"
  source {
    content  = file("${path.module}/lambda/daily_guardrail.py")
    filename = "main.py"
  }
  source {
    content  = file("${path.module}/lambda/requirements.txt")
    filename = "requirements.txt"
  }
}

resource "google_storage_bucket_object" "daily_guardrail" {
  name   = "daily_guardrail_${data.archive_file.daily_guardrail.output_md5}.zip"
  bucket = google_storage_bucket.functions.name
  source = data.archive_file.daily_guardrail.output_path
}

resource "google_cloudfunctions2_function" "daily_guardrail" {
  name     = "dino-app-daily-guardrail"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "handler"

    source {
      storage_source {
        bucket = google_storage_bucket.functions.name
        object = google_storage_bucket_object.daily_guardrail.name
      }
    }
  }

  service_config {
    min_instance_count    = 0
    max_instance_count    = 1
    available_memory      = "256M"
    timeout_seconds       = 30
    service_account_email = google_service_account.kill_run.email   # reuse existing SA

    environment_variables = {
      CLOUD_RUN_SERVICE = google_cloud_run_v2_service.app.name
      CLOUD_RUN_REGION  = var.region
      GCP_PROJECT       = var.project_id
    }
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = google_pubsub_topic.daily_alerts.id
    retry_policy   = "RETRY_POLICY_RETRY"
  }

  depends_on = [
    google_project_service.eventarc,
    google_project_service.cloudfunctions,
  ]
}

# ── Daily midnight restore ─────────────────────────────────────────────────────
# Cloud Scheduler fires at midnight UTC each day → restore_run re-enables Cloud Run.
# This resets the service after the daily billing window closes, so Myra can use
# the app again the next day even if it was killed by the guardrail.

resource "google_pubsub_topic" "restore_trigger" {
  name = "dino-app-restore-trigger"
}

data "archive_file" "restore_run" {
  type        = "zip"
  output_path = "${path.module}/lambda/restore_run.zip"
  source {
    content  = file("${path.module}/lambda/restore_run.py")
    filename = "main.py"
  }
  source {
    content  = file("${path.module}/lambda/requirements.txt")
    filename = "requirements.txt"
  }
}

resource "google_storage_bucket_object" "restore_run" {
  name   = "restore_run_${data.archive_file.restore_run.output_md5}.zip"
  bucket = google_storage_bucket.functions.name
  source = data.archive_file.restore_run.output_path
}

resource "google_service_account" "daily_restore" {
  account_id   = "dino-app-daily-restore"
  display_name = "Dino App Daily Restore Function"
}

resource "google_project_iam_member" "daily_restore_cloud_run" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.daily_restore.email}"
}

resource "google_project_iam_member" "daily_restore_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.daily_restore.email}"
}

resource "google_cloudfunctions2_function" "restore_run" {
  name     = "dino-app-restore-run"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "handler"

    source {
      storage_source {
        bucket = google_storage_bucket.functions.name
        object = google_storage_bucket_object.restore_run.name
      }
    }
  }

  service_config {
    min_instance_count    = 0
    max_instance_count    = 1
    available_memory      = "256M"
    timeout_seconds       = 30
    service_account_email = google_service_account.daily_restore.email

    environment_variables = {
      CLOUD_RUN_SERVICE = google_cloud_run_v2_service.app.name
      CLOUD_RUN_REGION  = var.region
      GCP_PROJECT       = var.project_id
      MAX_INSTANCES     = tostring(var.max_instances)
    }
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = google_pubsub_topic.restore_trigger.id
    retry_policy   = "RETRY_POLICY_DO_NOT_RETRY"
  }

  depends_on = [
    google_project_service.eventarc,
    google_project_service.cloudfunctions,
  ]
}

resource "google_cloud_scheduler_job" "daily_restore" {
  name             = "dino-app-daily-restore"
  description      = "Restore Cloud Run at midnight UTC after daily cost window resets"
  schedule         = "0 0 * * *"
  time_zone        = "UTC"
  attempt_deadline = "60s"

  pubsub_target {
    topic_name = google_pubsub_topic.restore_trigger.id
    data       = base64encode("{\"action\":\"restore\"}")
  }

  depends_on = [google_project_service.cloudscheduler]
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
