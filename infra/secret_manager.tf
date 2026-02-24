# Secret Manager (replaces SSM Parameter Store)
# App config values read at runtime by Cloud Run.

resource "google_secret_manager_secret" "child_name" {
  secret_id = "child-name"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "child_name" {
  secret      = google_secret_manager_secret.child_name.id
  secret_data = "Myra"
}

resource "google_secret_manager_secret" "similarity_threshold" {
  secret_id = "similarity-threshold"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "similarity_threshold" {
  secret      = google_secret_manager_secret.similarity_threshold.id
  secret_data = "50"
}

resource "google_secret_manager_secret" "max_attempts" {
  secret_id = "max-attempts"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "max_attempts" {
  secret      = google_secret_manager_secret.max_attempts.id
  secret_data = "3"
}

resource "google_secret_manager_secret" "languages" {
  secret_id = "languages"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "languages" {
  secret      = google_secret_manager_secret.languages.id
  secret_data = "telugu,assamese"
}

# Allow Cloud Run service account to read secrets
resource "google_project_iam_member" "cloud_run_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

data "google_project" "project" {
  project_id = var.project_id
  depends_on = [google_project_service.cloudresourcemanager]
}
