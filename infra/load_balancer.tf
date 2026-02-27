# Global HTTPS Load Balancer + Cloud CDN (replaces ALB + CloudFront)
# Routes internet traffic → Cloud Armor → Cloud Run backend.

# Reserve a global static IP for the load balancer
resource "google_compute_global_address" "app" {
  name = "dino-app-ip"
}

# Cloud Run network endpoint group — connects LB to Cloud Run
resource "google_compute_region_network_endpoint_group" "app" {
  name                  = "dino-app-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.app.name
  }
}

# Backend service with Cloud CDN and Cloud Armor attached
resource "google_compute_backend_service" "app" {
  name                  = "dino-app-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.app.self_link

  backend {
    group = google_compute_region_network_endpoint_group.app.id
  }

  # Cache static assets (HTML/CSS/JS), skip cache for /api/*
  cdn_policy {
    cache_mode                   = "USE_ORIGIN_HEADERS"
    signed_url_cache_max_age_sec = 0
  }

  enable_cdn = true

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# URL map — /api/* bypasses cache, everything else uses CDN
resource "google_compute_url_map" "app" {
  name            = "dino-app-urlmap"
  default_service = google_compute_backend_service.app.id

  host_rule {
    hosts        = ["*"]
    path_matcher = "paths"
  }

  path_matcher {
    name            = "paths"
    default_service = google_compute_backend_service.app.id
  }
}

# Redirect HTTP → HTTPS
resource "google_compute_url_map" "https_redirect" {
  name = "dino-app-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

# Regenerates a new suffix when the domain changes, enabling create_before_destroy
resource "random_id" "cert" {
  byte_length = 4
  keepers = {
    domain = var.domain != "" ? var.domain : "nip.io"
  }
}

# Managed SSL certificate (auto-provisioned by GCP)
resource "google_compute_managed_ssl_certificate" "app" {
  name = "dino-app-cert-${random_id.cert.hex}"

  managed {
    domains = var.domain != "" ? [var.domain] : ["${google_compute_global_address.app.address}.nip.io"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

# HTTPS frontend
resource "google_compute_target_https_proxy" "app" {
  name             = "dino-app-https-proxy"
  url_map          = google_compute_url_map.app.id
  ssl_certificates = [google_compute_managed_ssl_certificate.app.id]
}

# HTTP frontend (redirects to HTTPS)
resource "google_compute_target_http_proxy" "redirect" {
  name    = "dino-app-http-proxy"
  url_map = google_compute_url_map.https_redirect.id
}

# HTTPS forwarding rule (port 443)
resource "google_compute_global_forwarding_rule" "https" {
  name                  = "dino-app-https"
  target                = google_compute_target_https_proxy.app.id
  port_range            = "443"
  ip_address            = google_compute_global_address.app.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# HTTP forwarding rule (port 80, redirects to HTTPS)
resource "google_compute_global_forwarding_rule" "http" {
  name                  = "dino-app-http"
  target                = google_compute_target_http_proxy.redirect.id
  port_range            = "80"
  ip_address            = google_compute_global_address.app.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}
