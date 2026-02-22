output "app_url" {
  description = "App URL — share this with your family!"
  value       = "https://${aws_cloudfront_distribution.main.domain_name}"
}

output "ecr_repository_url" {
  description = "ECR repository URL — used in deploy/build-push.sh"
  value       = aws_ecr_repository.app.repository_url
}

output "alb_dns_name" {
  description = "ALB DNS (internal — only reachable from CloudFront IPs)"
  value       = aws_lb.main.dns_name
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.app.name
}

output "cloudwatch_logs_url" {
  description = "CloudWatch Logs URL for ECS container logs"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#logsV2:log-groups/log-group/%2Fecs%2F${var.prefix}"
}

output "restart_command" {
  description = "Run this to restart the app after a budget kill or manual scale-to-zero"
  value       = "aws ecs update-service --region ${var.aws_region} --cluster ${aws_ecs_cluster.main.name} --service ${aws_ecs_service.app.name} --desired-count 1"
}
