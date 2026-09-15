"""Static cost-safety checks for AWS heartbeat Terraform + CI (#216)."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MODULE = ROOT / "infra" / "aws-health-monitor"
MAIN_TF = MODULE / "main.tf"
VERSIONS_TF = MODULE / "versions.tf"
VARIABLES_TF = MODULE / "variables.tf"
OUTPUTS_TF = MODULE / "outputs.tf"
DOCS = ROOT / "docs" / "aws-health-monitor.md"
CRONJOB = ROOT / "k8s" / "aws-health-heartbeat.yaml"
KUSTOMIZATION = ROOT / "k8s" / "kustomization.yaml"


def _active_hcl(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return "\n".join(lines)


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_module_is_isolated_from_azure_terraform() -> None:
    main = MAIN_TF.read_text(encoding="utf-8")
    versions = VERSIONS_TF.read_text(encoding="utf-8")
    assert "azurerm" not in main
    assert "azurerm" not in versions
    assert "azure-rankings-archive" not in main


def test_required_outbound_resources_are_declared() -> None:
    active = _active_hcl(MAIN_TF)
    assert 'resource "aws_scheduler_schedule"' in active
    assert 'resource "aws_lambda_function"' in active
    assert 'resource "aws_dynamodb_table"' in active
    assert 'resource "aws_cloudwatch_log_group"' in active
    assert 'resource "aws_iam_role"' in active
    assert 'resource "aws_iam_user"' in active
    assert 'resource "aws_iam_user_policy"' in active
    assert "schedule_expression" in active
    assert "var.schedule_expression" in active


def test_no_health_url_or_home_target_variable() -> None:
    variables = VARIABLES_TF.read_text(encoding="utf-8")
    outputs = OUTPUTS_TF.read_text(encoding="utf-8")
    main = MAIN_TF.read_text(encoding="utf-8")
    for needle in ("health_url", "target_url", "HEALTH_URL", "home_url"):
        assert needle not in variables, needle
        assert needle not in outputs, needle
        assert needle not in main, needle


def test_schedule_is_locked_to_fifteen_minutes() -> None:
    variables = _norm(VARIABLES_TF.read_text(encoding="utf-8"))
    assert 'default = "rate(15 minutes)"' in variables
    assert 'var.schedule_expression == "rate(15 minutes)"' in variables


def test_dynamodb_is_standard_provisioned_one_rcu_wcu_with_ttl() -> None:
    active = _norm(_active_hcl(MAIN_TF))
    assert 'billing_mode = "PROVISIONED"' in active
    assert "read_capacity = 1" in active
    assert "write_capacity = 1" in active
    assert 'table_class = "STANDARD"' in active
    assert 'hash_key = "service_name"' in active
    assert 'range_key = "checked_at"' in active
    assert 'attribute_name = "expires_at"' in active
    assert "stream_enabled = false" in active
    assert "PAY_PER_REQUEST" not in active
    assert "global_secondary_index" not in active
    assert "aws_dax" not in active


def test_lambda_is_small_query_only_and_not_in_a_vpc() -> None:
    active = _norm(_active_hcl(MAIN_TF))
    variables = _norm(VARIABLES_TF.read_text(encoding="utf-8"))
    assert "memory_size = var.lambda_memory_mb" in active
    assert "timeout = var.lambda_timeout_seconds" in active
    assert "default = 128" in variables
    assert "var.lambda_memory_mb == 128" in variables
    assert "default = 5" in variables
    assert "vpc_config" not in active
    assert "provisioned_concurrency" not in active.lower()
    assert "tracing_config" not in active
    assert 'runtime = "python3.12"' in active
    assert "STALE_AFTER_SECONDS" in active
    assert '"dynamodb:Query"' in active
    assert '"dynamodb:PutItem"' in active  # home IAM user only
    # Lambda policy must Query; PutItem belongs to the heartbeat IAM user policy.
    lambda_policy = active.split("data \"aws_iam_policy_document\" \"lambda_runtime\"")[1].split(
        "data \"aws_iam_policy_document\""
    )[0]
    assert "dynamodb:Query" in lambda_policy
    assert "dynamodb:PutItem" not in lambda_policy


def test_home_iam_user_is_putitem_only() -> None:
    active = _norm(_active_hcl(MAIN_TF))
    sender_policy = active.split(
        "data \"aws_iam_policy_document\" \"heartbeat_sender\""
    )[1].split("resource \"")[0]
    assert "dynamodb:PutItem" in sender_policy
    assert "dynamodb:Query" not in sender_policy
    assert "dynamodb:Scan" not in sender_policy
    assert "aws_iam_access_key" not in active


def test_log_group_retention_is_short() -> None:
    variables = _norm(VARIABLES_TF.read_text(encoding="utf-8"))
    assert "retention_in_days = var.log_retention_days" in _norm(_active_hcl(MAIN_TF))
    assert "default = 3" in variables


def test_scheduler_retries_are_at_most_one() -> None:
    active = _norm(_active_hcl(MAIN_TF))
    variables = VARIABLES_TF.read_text(encoding="utf-8")
    assert "maximum_retry_attempts = var.scheduler_retry_attempts" in active
    assert "var.scheduler_retry_attempts == 0 || var.scheduler_retry_attempts == 1" in variables
    assert 'mode = "OFF"' in active


def test_iam_avoids_wildcards() -> None:
    active = _active_hcl(MAIN_TF)
    assert 'resources = ["*"]' not in active
    assert "aws_dynamodb_table.heartbeats.arn" in active
    assert "aws_lambda_function.stale_check.arn" in active


def test_forbidden_billable_services_are_absent() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (MAIN_TF, VERSIONS_TF, VARIABLES_TF, OUTPUTS_TF)
    )
    active = "\n".join(
        line.strip()
        for line in combined.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    forbidden = (
        "aws_nat_gateway",
        "aws_eip",
        "aws_apigateway",
        "aws_api_gateway",
        "aws_apigatewayv2",
        "aws_sns_",
        "aws_sqs_",
        "aws_sfn_",
        "aws_secretsmanager",
        "aws_vpc",
        "aws_subnet",
        "aws_security_group",
        "aws_lb",
        "aws_elb",
        "aws_instance",
        "aws_cloudwatch_dashboard",
        "aws_cloudwatch_metric_alarm",
        "aws_dynamodb_global_table",
        "aws_cloudwatch_event_rule",
    )
    for name in forbidden:
        assert name not in active, name


def test_optional_budget_is_not_required() -> None:
    variables = _norm(VARIABLES_TF.read_text(encoding="utf-8"))
    active = _active_hcl(MAIN_TF)
    assert 'default = ""' in variables
    assert "count = var.budget_notification_email != \"\" ? 1 : 0" in active


def test_docs_state_outbound_only_and_free_tier_is_not_a_cap() -> None:
    docs = DOCS.read_text(encoding="utf-8")
    assert "AWS never connects back" in docs or "AWS never call" in docs.lower()
    assert "allowance, not a hard billing cap" in docs
    assert "~2,880" in docs
    assert "terraform destroy" in docs
    assert "NAT Gateway" in docs
    assert "health_url" in docs.lower()  # explicitly saying there is no such var
    assert "no" in docs.lower() and "health_url" in docs


def test_cronjob_is_suspended_and_uses_in_cluster_health() -> None:
    text = CRONJOB.read_text(encoding="utf-8")
    assert "suspend: true" in text
    assert "http://stock-ticker-backend:8000/health" in text
    assert "*/15 * * * *" in text
    assert "image: stock-ticker-backend:local" in text
    assert "imagePullPolicy: Never" in text
    assert "python:3.12-alpine" not in text
    assert "key: SERVICE_NAME" in text
    assert "key: TTL_DAYS" in text
    assert "stock-ticker-aws-health-heartbeat-script" in text
    kustomization = KUSTOMIZATION.read_text(encoding="utf-8")
    assert "aws-health-heartbeat.yaml" in kustomization
    assert "stock-ticker-aws-health-heartbeat-script" in kustomization


def test_ttl_days_is_documented_output_not_lambda_env() -> None:
    outputs = OUTPUTS_TF.read_text(encoding="utf-8")
    variables = VARIABLES_TF.read_text(encoding="utf-8")
    main = MAIN_TF.read_text(encoding="utf-8")
    assert 'output "ttl_days"' in outputs
    assert "TTL_DAYS" in variables or "ttl_days" in variables
    # Lambda must not own retention; home PutItem sets expires_at.
    lambda_env = main.split('resource "aws_lambda_function" "stale_check"')[1].split(
        "depends_on"
    )[0]
    assert "TTL_DAYS" not in lambda_env
    assert "STALE_AFTER_SECONDS" in lambda_env


def test_readme_and_module_readme_link_the_doc() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    module_readme = MODULE / "README.md"
    assert "docs/aws-health-monitor.md" in readme
    assert "heartbeat" in readme.lower()
    assert module_readme.is_file()
    assert "docs/aws-health-monitor.md" in module_readme.read_text(encoding="utf-8")
    assert "never calls the home node" in module_readme.read_text(encoding="utf-8").lower()


def test_workflow_does_not_apply_or_invoke_live_aws() -> None:
    for rel in (".github/workflows/test-and-smoke.yml", ".github/workflows/publish-ghcr.yml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "aws-health-monitor" not in text
        assert "terraform apply" not in text
        assert "AWS_ACCESS_KEY" not in text
        assert "aws lambda invoke" not in text.lower()
