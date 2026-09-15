output "aws_region" {
  description = "Region for the home heartbeat sender (AWS_REGION)."
  value       = var.aws_region
}

output "dynamodb_table_name" {
  description = "Provisioned DynamoDB table for home-node heartbeat records (TABLE_NAME)."
  value       = aws_dynamodb_table.heartbeats.name
}

output "dynamodb_table_arn" {
  description = "DynamoDB table ARN."
  value       = aws_dynamodb_table.heartbeats.arn
}

output "heartbeat_iam_user_name" {
  description = "IAM user for the home CronJob. Create an access key outside Terraform."
  value       = aws_iam_user.heartbeat_sender.name
}

output "heartbeat_iam_user_arn" {
  description = "IAM user ARN for the home heartbeat sender."
  value       = aws_iam_user.heartbeat_sender.arn
}

output "create_access_key_command" {
  description = "Operator command to create a PutItem-only access key (secret is shown once by AWS)."
  value       = "aws iam create-access-key --user-name ${aws_iam_user.heartbeat_sender.name}"
}

output "lambda_function_name" {
  description = "Stale-heartbeat Lambda (does not call the home node)."
  value       = aws_lambda_function.stale_check.function_name
}

output "lambda_function_arn" {
  description = "Lambda ARN (Scheduler target)."
  value       = aws_lambda_function.stale_check.arn
}

output "schedule_name" {
  description = "EventBridge Scheduler name (rate 15 minutes)."
  value       = aws_scheduler_schedule.stale_check.name
}

output "log_group_name" {
  description = "CloudWatch log group with short retention."
  value       = aws_cloudwatch_log_group.stale_check.name
}

output "service_name" {
  description = "Heartbeat partition key / SERVICE_NAME for the CronJob Secret."
  value       = var.service_name
}

output "ttl_days" {
  description = "Suggested TTL_DAYS for the CronJob Secret (expires_at on each PutItem)."
  value       = var.ttl_days
}

output "stale_after_seconds" {
  description = "Age threshold used by the Lambda classifier."
  value       = var.stale_after_seconds
}

output "budget_name" {
  description = "Optional AWS Budget name, if an email was provided."
  value       = try(aws_budgets_budget.monthly[0].name, null)
}

output "intended_resources" {
  description = "Expected terraform-managed resource types for cost-safety review."
  value = [
    "aws_dynamodb_table",
    "aws_lambda_function",
    "aws_lambda_function_event_invoke_config",
    "aws_cloudwatch_log_group",
    "aws_iam_role",
    "aws_iam_role_policy",
    "aws_iam_user",
    "aws_iam_user_policy",
    "aws_scheduler_schedule",
    "aws_budgets_budget (optional)",
  ]
}

output "query_recent_heartbeats" {
  description = "CLI to inspect the latest heartbeat records without a table scan."
  value       = "aws dynamodb query --table-name ${aws_dynamodb_table.heartbeats.name} --key-condition-expression 'service_name = :s' --expression-attribute-values '{\":s\":{\"S\":\"${var.service_name}\"}}' --scan-index-forward false --limit 10"
}
