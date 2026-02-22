# ── SNS topic for budget alerts ───────────────────────────────────────────────
resource "aws_sns_topic" "budget_alerts" {
  name = "${var.prefix}-budget-alerts"
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn = aws_sns_topic.budget_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowBudgetsPublish"
      Effect = "Allow"
      Principal = {
        Service = "budgets.amazonaws.com"
      }
      Action   = "SNS:Publish"
      Resource = aws_sns_topic.budget_alerts.arn
    }]
  })
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ── Lambda kill-switch ────────────────────────────────────────────────────────
data "archive_file" "kill_ecs" {
  type        = "zip"
  source_file = "${path.module}/lambda/kill_ecs.py"
  output_path = "${path.module}/lambda/kill_ecs.zip"
}

resource "aws_lambda_function" "kill_ecs" {
  filename         = data.archive_file.kill_ecs.output_path
  source_code_hash = data.archive_file.kill_ecs.output_base64sha256

  function_name = "${var.prefix}-kill-ecs"
  role          = aws_iam_role.kill_lambda.arn
  handler       = "kill_ecs.handler"
  runtime       = "python3.11"
  timeout       = 30

  environment {
    variables = {
      ECS_CLUSTER = aws_ecs_cluster.main.name
      ECS_SERVICE = aws_ecs_service.app.name
      ECS_REGION  = var.aws_region
    }
  }
}

resource "aws_sns_topic_subscription" "kill_lambda" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.kill_ecs.arn
}

resource "aws_lambda_permission" "sns_invoke" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.kill_ecs.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.budget_alerts.arn
}

# ── Budget: $50/month hard cap ────────────────────────────────────────────────
resource "aws_budgets_budget" "monthly" {
  name         = "${var.prefix}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # $40 warning (80%) — email only
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # $50 kill (100%) — SNS triggers email + Lambda kill-switch
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }
}
