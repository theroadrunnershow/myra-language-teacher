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
  description = "Cloud Run CPU allocation (1 = 1 vCPU)"
  type        = string
  default     = "1"
}

variable "container_memory" {
  description = "Cloud Run memory allocation (Whisper base needs ~2GB)"
  type        = string
  default     = "3Gi"
}

variable "max_instances" {
  description = "Maximum number of Cloud Run instances (hard cap on compute cost)"
  type        = number
  default     = 2
}

variable "budget_limit" {
  description = "Monthly budget limit in USD - kill-switch fires when this is hit"
  type        = number
  default     = 50
}
