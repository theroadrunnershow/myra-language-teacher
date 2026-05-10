# Cloud Run service (replaces ECS Fargate + VPC + NAT Gateway + EventBridge Scheduler)
# Scale-to-zero is built-in — no nightly scheduler needed.

resource "google_cloud_run_v2_service" "app" {
  name     = "dino-app"
  location = var.region

  # Accept all traffic directly — no load balancer needed for a hobby project.
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = 0 # scale to zero when idle
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
        cpu_idle = true # only allocate CPU during request processing
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

      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6 # 30s total startup window
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
