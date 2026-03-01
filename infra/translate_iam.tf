# Grant the Cloud Run default service account permission to call Cloud Translate API.
# The default SA is the Compute Engine default SA:
#   {project_number}-compute@developer.gserviceaccount.com

data "google_compute_default_service_account" "default" {
  depends_on = [google_project_service.compute]
}

resource "google_project_iam_member" "cloud_run_translate" {
  project = var.project_id
  role    = "roles/cloudtranslate.user"
  member  = "serviceAccount:${data.google_compute_default_service_account.default.email}"
}
