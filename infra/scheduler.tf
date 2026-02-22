resource "aws_scheduler_schedule_group" "main" {
  name = var.prefix
}

# ── Scale DOWN: 8 PM PST (04:00 UTC) → desired = 0 ───────────────────────────
resource "aws_scheduler_schedule" "scale_down" {
  name        = "${var.prefix}-scale-down"
  group_name  = aws_scheduler_schedule_group.main.name
  description = "Scale ECS to 0 at 8 PM PST (04:00 UTC)"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.scale_down_cron
  schedule_expression_timezone = "UTC"

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:updateService"
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      Cluster      = aws_ecs_cluster.main.name
      Service      = aws_ecs_service.app.name
      DesiredCount = 0
    })
  }
}

# ── Scale UP: 7:30 AM PST (15:30 UTC) → desired = 1 ─────────────────────────
resource "aws_scheduler_schedule" "scale_up" {
  name        = "${var.prefix}-scale-up"
  group_name  = aws_scheduler_schedule_group.main.name
  description = "Scale ECS to 1 at 7:30 AM PST (15:30 UTC)"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.scale_up_cron
  schedule_expression_timezone = "UTC"

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:updateService"
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      Cluster      = aws_ecs_cluster.main.name
      Service      = aws_ecs_service.app.name
      DesiredCount = 1
    })
  }
}
