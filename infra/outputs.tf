output "app_url" {
  description = "App URL via Global HTTPS Load Balancer — share this with your family!"
  value       = "https://${google_compute_global_address.app.address}.nip.io"
}

output "cloud_run_url" {
  description = "Direct Cloud Run URL (no CDN/WAF — use for testing only)"
  value       = google_cloud_run_v2_service.app.uri
}

output "registry_url" {
  description = "Artifact Registry URL — used for docker push"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/myra/dino-app"
}

output "cloud_run_service_name" {
  description = "Cloud Run service name"
  value       = google_cloud_run_v2_service.app.name
}

output "cloud_run_region" {
  description = "Cloud Run region"
  value       = var.region
}

output "logs_url" {
  description = "GCP Cloud Logging URL for Cloud Run logs"
  value       = "https://console.cloud.google.com/run/detail/${var.region}/${google_cloud_run_v2_service.app.name}/logs?project=${var.project_id}"
}

output "restart_command" {
  description = "Run this to re-enable Cloud Run after a budget kill-switch"
  value       = "gcloud run services update ${google_cloud_run_v2_service.app.name} --region=${var.region} --max-instances=${var.max_instances} --project=${var.project_id}"
}

output "docker_push_command" {
  description = "Commands to build and push the Docker image"
  value       = <<-EOT
    gcloud auth configure-docker ${var.region}-docker.pkg.dev
    docker build -t ${var.region}-docker.pkg.dev/${var.project_id}/myra/dino-app:latest .
    docker push ${var.region}-docker.pkg.dev/${var.project_id}/myra/dino-app:latest
  EOT
}
