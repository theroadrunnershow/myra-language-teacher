# ── WAF Web ACL (must live in us-east-1 for CloudFront) ──────────────────────
resource "aws_wafv2_web_acl" "main" {
  provider    = aws.us_east_1
  name        = "${var.prefix}-waf"
  description = "Rate-limiting WAF for Myra Language Teacher"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # Rule 1: /api/recognize — 10 req/min per IP
  rule {
    name     = "rate-limit-recognize"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = 10
        aggregate_key_type    = "IP"
        evaluation_window_sec = 60

        scope_down_statement {
          byte_match_statement {
            field_to_match {
              uri_path {}
            }
            positional_constraint = "STARTS_WITH"
            search_string         = "/api/recognize"
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.prefix}-rate-recognize"
      sampled_requests_enabled   = true
    }
  }

  # Rule 2: /api/tts — 30 req/min per IP
  rule {
    name     = "rate-limit-tts"
    priority = 2

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = 30
        aggregate_key_type    = "IP"
        evaluation_window_sec = 60

        scope_down_statement {
          byte_match_statement {
            field_to_match {
              uri_path {}
            }
            positional_constraint = "STARTS_WITH"
            search_string         = "/api/tts"
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.prefix}-rate-tts"
      sampled_requests_enabled   = true
    }
  }

  # Rule 3: all /api/* — 100 req/min per IP
  rule {
    name     = "rate-limit-api"
    priority = 3

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = 100
        aggregate_key_type    = "IP"
        evaluation_window_sec = 60

        scope_down_statement {
          byte_match_statement {
            field_to_match {
              uri_path {}
            }
            positional_constraint = "STARTS_WITH"
            search_string         = "/api/"
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.prefix}-rate-api"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.prefix}-waf"
    sampled_requests_enabled   = true
  }

  tags = { Name = "${var.prefix}-waf" }
}

# ── CloudFront Distribution ───────────────────────────────────────────────────
resource "aws_cloudfront_distribution" "main" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "Myra Language Teacher"
  web_acl_id      = aws_wafv2_web_acl.main.arn

  origin {
    domain_name = aws_lb.main.dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # /api/* — never cache, forward everything (audio POST, query strings)
  ordered_cache_behavior {
    path_pattern     = "/api/*"
    target_origin_id = "alb"

    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = true
      headers      = ["*"]
      cookies { forward = "all" }
    }

    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  # Default — cache static assets at the edge
  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 86400
    max_ttl     = 604800
  }

  price_class = "PriceClass_100"

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "${var.prefix}-cf" }
}
