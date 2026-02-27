variable "project_id" {
  description = "GCP project ID (e.g. myra-language-teacher-123456)"
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run and Artifact Registry"
  type        = string
  default     = "us-west1"
}

variable "alert_email" {
  description = "Email for budget warning and kill-switch notification"
  type        = string
  default     = "theroadrunnershow@gmail.com"
}

variable "container_cpu" {
  description = "Cloud Run CPU allocation (2 = 2 vCPU)"
  type        = string
  default     = "2"
}

variable "container_memory" {
  description = "Cloud Run memory allocation (Whisper tiny needs ~1GB)"
  type        = string
  default     = "3Gi"
}

variable "max_instances" {
  description = "Maximum number of Cloud Run instances (hard cap on compute cost)"
  type        = number
  default     = 2
}

variable "billing_account_id" {
  description = "GCP billing account ID (format: XXXXXX-XXXXXX-XXXXXX) — find in Console → Billing"
  type        = string
}

variable "budget_limit" {
  description = "Monthly budget limit in USD - kill-switch fires when this is hit"
  type        = number
  default     = 50
}

variable "daily_budget_limit" {
  description = "Daily spend limit in USD - kill-switch fires when 24-hour cost delta exceeds this"
  type        = number
  default     = 20
}

variable "domain" {
  description = "Custom domain name (e.g. kiddos-telugu-teacher.com) — leave empty to use nip.io fallback"
  type        = string
  default     = ""
}
