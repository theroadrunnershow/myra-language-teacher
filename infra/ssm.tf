resource "aws_ssm_parameter" "child_name" {
  name        = "/${var.prefix}/child_name"
  type        = "String"
  value       = "Myra"
  description = "Child's name shown in the app UI and spoken prompts"
}

resource "aws_ssm_parameter" "similarity_threshold" {
  name        = "/${var.prefix}/similarity_threshold"
  type        = "String"
  value       = "50"
  description = "Fuzzy-match score required to count pronunciation as correct (0-100)"
}

resource "aws_ssm_parameter" "max_attempts" {
  name        = "/${var.prefix}/max_attempts"
  type        = "String"
  value       = "3"
  description = "Max pronunciation attempts before auto-advancing to next word"
}

resource "aws_ssm_parameter" "languages" {
  name        = "/${var.prefix}/languages"
  type        = "String"
  value       = "telugu,assamese"
  description = "Comma-separated list of active languages"
}
