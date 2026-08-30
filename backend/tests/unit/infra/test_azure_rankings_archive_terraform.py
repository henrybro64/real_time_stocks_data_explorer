"""Static cost-safety checks for Azure rankings archive Terraform + CI (#186/#187)."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MAIN_TF = ROOT / "infra" / "azure-rankings-archive" / "main.tf"
VERSIONS_TF = ROOT / "infra" / "azure-rankings-archive" / "versions.tf"
VARIABLES_TF = ROOT / "infra" / "azure-rankings-archive" / "variables.tf"
WORKFLOW = ROOT / ".github" / "workflows" / "test-and-smoke.yml"


def _active_hcl_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_terraform_does_not_use_unsupported_azurerm_free_offer_args() -> None:
    """Released AzureRM schema rejects Free Offer args — must not appear on resources."""
    text = MAIN_TF.read_text(encoding="utf-8")
    active = "\n".join(_active_hcl_lines(text))
    # Unsupported on released azurerm_mssql_database for this module's pin.
    assert "use_free_limit" not in active
    assert "free_limit_exhaustion_behavior" not in active
    # Fail-closed: do not manage/create the DB via azurerm_mssql_database.
    assert "resource \"azurerm_mssql_database\"" not in active
    assert "data \"azurerm_mssql_database\"" not in active
    assert "removed" in active
    assert "destroy = false" in active
    assert "rankings_archive_free" in text


def test_terraform_defaults_to_rankings_archive_free_not_paid_name() -> None:
    variables = VARIABLES_TF.read_text(encoding="utf-8")
    assert 'default     = "rankings_archive_free"' in variables
    assert 'var.sql_database_name != "rankings_archive"' in variables


def test_versions_tf_does_not_claim_azurerm_free_offer_support() -> None:
    versions = VERSIONS_TF.read_text(encoding="utf-8")
    assert "does NOT support Free Offer" in versions
    assert "required_version = \">= 1.7.0\"" in versions
    assert "use_free_limit" not in "\n".join(
        line for line in versions.splitlines() if not line.strip().startswith("#")
    )


def test_workflow_live_azure_sql_smoke_is_manual_only() -> None:
    """Push/PR/schedule must not wake live Azure SQL (#186 / #187)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    # No workflow-level schedule trigger.
    assert "\nschedule:" not in text and not text.strip().startswith("schedule:")
    assert "cron:" not in text

    # Extract the azure-rankings-archive-smoke job `if:` block (first multiline if).
    marker = "azure-rankings-archive-smoke:"
    assert marker in text
    after = text.split(marker, 1)[1]
    # Job if condition must require workflow_dispatch only.
    assert "github.event_name == 'workflow_dispatch'" in after
    # Must not re-enable push or schedule for this job.
    job_header = after.split("steps:", 1)[0]
    assert "github.event_name == 'push'" not in job_header
    assert "github.event_name == 'schedule'" not in job_header
    assert "Cost warning" in after or "cost warning" in after.lower()
