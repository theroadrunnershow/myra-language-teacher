# Cloud Armor security policy (replaces WAF Web ACL on CloudFront)
# Rate limits mirror the original WAF rules.

resource "google_compute_security_policy" "app" {
  name        = "dino-app-armor"
  description = "Rate limiting and OWASP protection for Myra language teacher app"

  # ── OWASP preconfigured rule set (priority 900, before rate limits) ───────────
  # Blocks requests matching known XSS, SQLi, LFI, RFI, and scanner signatures.
  # These evaluate request bodies + headers against OWASP ModSecurity CRS rules.
  rule {
    action   = "deny(403)"
    priority = 900
    match {
      expr {
        expression = join(" || ", [
          "evaluatePreconfiguredExpr('xss-stable')",
          "evaluatePreconfiguredExpr('sqli-stable')",
          "evaluatePreconfiguredExpr('lfi-stable')",
          "evaluatePreconfiguredExpr('rfi-stable')",
          "evaluatePreconfiguredExpr('scannerdetection-stable')",
        ])
      }
    }
    description = "OWASP CRS: XSS, SQLi, LFI, RFI, scanner detection"
  }

  # STT endpoint — most expensive, tightest limit (10 req/min per IP)
  rule {
    action   = "rate_based_ban"
    priority = 1000
    match {
      expr {
        expression = "request.path.matches('/api/recognize')"
      }
    }
    rate_limit_options {
      rate_limit_threshold {
        count        = 10
        interval_sec = 60
      }
      ban_duration_sec = 120
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
    }
    description = "Rate limit STT: 10 req/min per IP"
  }

  # TTS endpoint (30 req/min per IP)
  rule {
    action   = "rate_based_ban"
    priority = 1001
    match {
      expr {
        expression = "request.path.matches('/api/tts')"
      }
    }
    rate_limit_options {
      rate_limit_threshold {
        count        = 30
        interval_sec = 60
      }
      ban_duration_sec = 120
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
    }
    description = "Rate limit TTS: 30 req/min per IP"
  }

  # Dino voice TTS endpoint — same cost as /api/tts, same budget (30 req/min per IP)
  rule {
    action   = "rate_based_ban"
    priority = 1002
    match {
      expr {
        expression = "request.path.matches('/api/dino-voice')"
      }
    }
    rate_limit_options {
      rate_limit_threshold {
        count        = 30
        interval_sec = 60
      }
      ban_duration_sec = 120
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
    }
    description = "Rate limit dino-voice TTS: 30 req/min per IP"
  }

  # General API (100 req/min per IP)
  rule {
    action   = "rate_based_ban"
    priority = 1003
    match {
      expr {
        expression = "request.path.matches('/api/')"
      }
    }
    rate_limit_options {
      rate_limit_threshold {
        count        = 100
        interval_sec = 60
      }
      ban_duration_sec = 60
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
    }
    description = "Rate limit general API: 100 req/min per IP"
  }

  # Default: allow all other traffic
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow"
  }
}
