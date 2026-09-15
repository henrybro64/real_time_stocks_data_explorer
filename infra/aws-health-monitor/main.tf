locals {
  name           = var.name_prefix
  function_name  = "${var.name_prefix}-stale-check"
  table_name     = "${var.name_prefix}-heartbeats"
  schedule_group = "default"
  schedule_name  = "${var.name_prefix}-every-15m"
  sender_user    = "${var.name_prefix}-heartbeat"
}

data "aws_caller_identity" "current" {}

data "archive_file" "stale_check" {
  type        = "zip"
  source_file = "${path.module}/lambda/stale_check.py"
  # Same directory as the module (exists on a fresh checkout). Nested build/
  # is gitignored at repo root and is not created by the archive provider.
  output_path = "${path.module}/stale_check.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_runtime" {
  statement {
    sid = "WriteOwnLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.stale_check.arn}:*",
    ]
  }

  statement {
    sid = "ReadNewestHeartbeat"
    actions = [
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.heartbeats.arn]
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    # SourceAccount only. CreateSchedule validates AssumeRole before the
    # schedule exists, so aws:SourceArn on the schedule ARN is rejected with
    # "The execution role you provide must allow AWS EventBridge Scheduler to assume the role."
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    sid       = "InvokeStaleCheckLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.stale_check.arn]
  }
}

data "aws_iam_policy_document" "heartbeat_sender" {
  statement {
    sid       = "PutHeartbeatOnly"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.heartbeats.arn]
  }
}

resource "aws_dynamodb_table" "heartbeats" {
  name         = local.table_name
  billing_mode = "PROVISIONED"
  # 1/1 stays inside the DynamoDB Free Tier 25 RCU / 25 WCU allowance.
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "service_name"
  range_key      = "checked_at"
  table_class    = "STANDARD"

  attribute {
    name = "service_name"
    type = "S"
  }

  attribute {
    name = "checked_at"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  stream_enabled              = false
  deletion_protection_enabled = false

  point_in_time_recovery {
    enabled = false
  }

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "stale_check" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.name}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_runtime.json
}

resource "aws_lambda_function" "stale_check" {
  function_name    = local.function_name
  description      = "Classify newest home-node heartbeat as recent/stale/missing (#216). Does not call home."
  role             = aws_iam_role.lambda.arn
  filename         = data.archive_file.stale_check.output_path
  source_code_hash = data.archive_file.stale_check.output_base64sha256
  handler          = "stale_check.handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_seconds
  publish          = false
  layers           = []

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.heartbeats.name
      SERVICE_NAME        = var.service_name
      STALE_AFTER_SECONDS = tostring(var.stale_after_seconds)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.stale_check,
    aws_iam_role_policy.lambda,
  ]

  tags = var.tags
}

# Scheduler invokes Lambda asynchronously. Default async retry is 2 extra
# attempts on unhandled errors; v1 keeps retries on the schedule only (0 or 1).
resource "aws_lambda_function_event_invoke_config" "stale_check" {
  function_name          = aws_lambda_function.stale_check.function_name
  maximum_retry_attempts = 0
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.name}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "stale_check" {
  name                         = local.schedule_name
  description                  = "Classify newest home heartbeat every 15 minutes (#216)."
  group_name                   = local.schedule_group
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "UTC"
  state                        = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.stale_check.arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_event_age_in_seconds = 90
      maximum_retry_attempts       = var.scheduler_retry_attempts
    }
  }
}

# Home-node writer. Access keys are created outside Terraform and stored in a
# Kubernetes Secret — never committed and never outputted here.
# force_destroy allows terraform destroy while those external keys still exist.
resource "aws_iam_user" "heartbeat_sender" {
  name          = local.sender_user
  force_destroy = true
  tags          = var.tags
}

resource "aws_iam_user_policy" "heartbeat_sender" {
  name   = "${local.name}-heartbeat-put"
  user   = aws_iam_user.heartbeat_sender.name
  policy = data.aws_iam_policy_document.heartbeat_sender.json
}

# Optional. First two AWS Budgets per account are free. Core monitoring
# does not depend on this resource.
resource "aws_budgets_budget" "monthly" {
  count = var.budget_notification_email != "" ? 1 : 0

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = "1"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 1
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
