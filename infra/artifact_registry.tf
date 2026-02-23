# Container image repository (replaces ECR)
resource "google_artifact_registry_repository" "app" {
  repository_id = "myra"
  format        = "DOCKER"
  location      = var.region
  description   = "Myra language teacher app images"

  cleanup_policies {
    id     = "keep-5-most-recent"
    action = "KEEP"

    most_recent_versions {
      keep_count = 5
    }
  }
}
