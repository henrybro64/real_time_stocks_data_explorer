variable "aws_region" {
  type        = string
  description = "AWS region for this isolated health-monitor stack."
  default     = "us-east-1"
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix."
  default     = "stock-ticker-health"
}

variable "service_name" {
  type        = string
  description = "DynamoDB partition key value written by the home heartbeat sender."
  default     = "stock-ticker-backend"
}

variable "schedule_expression" {
  type        = string
  description = "EventBridge Scheduler rate for the stale-heartbeat Lambda."
  default     = "rate(15 minutes)"

  validation {
    condition     = var.schedule_expression == "rate(15 minutes)"
    error_message = "v1 allows only rate(15 minutes) so usage stays ~2,880 invocations/month."
  }
}

variable "ttl_days" {
  type        = number
  description = "Suggested heartbeat retention in days. Copy into the CronJob Secret as TTL_DAYS so each PutItem sets expires_at. DynamoDB only enables the TTL attribute; it does not enforce the day count by itself."
  default     = 7

  validation {
    condition     = var.ttl_days == floor(var.ttl_days) && var.ttl_days >= 1 && var.ttl_days <= 30
    error_message = "ttl_days must be a whole number between 1 and 30."
  }
}

variable "stale_after_seconds" {
  type        = number
  description = "Heartbeat older than this is classified stale (default 30 minutes)."
  default     = 1800

  validation {
    condition     = var.stale_after_seconds == floor(var.stale_after_seconds) && var.stale_after_seconds >= 900 && var.stale_after_seconds <= 7200
    error_message = "stale_after_seconds must be a whole number between 900 and 7200."
  }
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 5

  validation {
    condition     = var.lambda_timeout_seconds >= 3 && var.lambda_timeout_seconds <= 5
    error_message = "Lambda timeout must be 3–5 seconds."
  }
}

variable "lambda_memory_mb" {
  type    = number
  default = 128

  validation {
    condition     = var.lambda_memory_mb == 128
    error_message = "v1 Lambda memory is fixed at 128 MB."
  }
}

variable "log_retention_days" {
  type    = number
  default = 3

  validation {
    condition     = contains([1, 3, 5, 7], var.log_retention_days)
    error_message = "Log retention must be a short CloudWatch period (1, 3, 5, or 7 days)."
  }
}

variable "scheduler_retry_attempts" {
  type    = number
  default = 1

  validation {
    condition     = var.scheduler_retry_attempts == 0 || var.scheduler_retry_attempts == 1
    error_message = "Scheduler retries must be 0 or 1."
  }
}

variable "budget_notification_email" {
  type        = string
  description = "If set, create one optional AWS Budget ($1 monthly) with an email alert. First two budgets are free."
  default     = ""
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to supported resources."
  default = {
    project = "stock-ticker"
    purpose = "outbound-heartbeat-monitor"
    issue   = "216"
  }
}
