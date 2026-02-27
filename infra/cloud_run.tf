# Cloud Run service (replaces ECS Fargate + VPC + NAT Gateway + EventBridge Scheduler)
# Scale-to-zero is built-in — no nightly scheduler needed.

resource "google_cloud_run_v2_service" "app" {
  name     = "dino-app"
  location = var.region

  # Only accept traffic from the Global Load Balancer (and internal GCP services).
  # This prevents direct *.run.app URL access that would bypass Cloud Armor WAF.
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    scaling {
      min_instance_count = 0   # scale to zero when idle
      max_instance_count = var.max_instances
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/myra-language-teacher/dino-app:latest"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = var.container_cpu
          memory = var.container_memory
        }
        cpu_idle = true   # only allocate CPU during request processing
      }

      # App config — equivalent to SSM Parameter Store values at runtime
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "DISABLE_PASS1"
        value = "true"
      }

      # Startup probe — Whisper model load takes ~30s
      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        initial_delay_seconds = 10
        period_seconds        = 10
        failure_threshold     = 12   # 120s total startup window
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        period_seconds    = 30
        failure_threshold = 3
      }
    }

    # 5-minute request timeout (generous for Whisper STT on cold start)
    timeout = "300s"
  }

  depends_on = [google_artifact_registry_repository.app]
}

# Allow unauthenticated public access (it's a public-facing app)
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
