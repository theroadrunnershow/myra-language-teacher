variable "prefix" {
  description = "Resource name prefix for all AWS resources (e.g. 'dino-app' -> dino-app-ecr, dino-app-alb)"
  type        = string
  default     = "dino-app"
}

variable "aws_region" {
  description = "Primary AWS region"
  type        = string
  default     = "us-west-2"
}

variable "alert_email" {
  description = "Email for $40 budget warning and $50 kill-switch notification. REPLACE before deploying."
  type        = string
  default     = "theroadrunnershow@gmail.com"
}

variable "container_cpu" {
  description = "ECS Fargate task CPU units (1024 = 1 vCPU)"
  type        = number
  default     = 1024
}

variable "container_memory" {
  description = "ECS Fargate task memory in MB (3072 = 3 GB, enough for Whisper base + FastAPI)"
  type        = number
  default     = 3072
}

variable "ecs_min_tasks" {
  description = "Minimum number of running ECS tasks"
  type        = number
  default     = 1
}

variable "ecs_max_tasks" {
  description = "Maximum number of running ECS tasks (hard cap on compute cost)"
  type        = number
  default     = 2
}

variable "budget_limit" {
  description = "Monthly budget limit in USD - kill-switch fires when this is hit"
  type        = number
  default     = 50
}

# 8:00 PM PST = 04:00 UTC  |  8:00 PM PDT = 03:00 UTC
variable "scale_down_cron" {
  description = "Cron expression (UTC) for nightly scale-to-zero"
  type        = string
  default     = "cron(0 4 * * ? *)"
}

# 7:30 AM PST = 15:30 UTC  |  7:30 AM PDT = 14:30 UTC
variable "scale_up_cron" {
  description = "Cron expression (UTC) for morning scale-up"
  type        = string
  default     = "cron(30 15 * * ? *)"
}
