"""Static cost-safety checks for Azure rankings archive Terraform (#186)."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MAIN_TF = ROOT / "infra" / "azure-rankings-archive" / "main.tf"


def test_terraform_keeps_azure_sql_free_offer_guardrails() -> None:
    text = MAIN_TF.read_text(encoding="utf-8")
    active_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    joined = "\n".join(active_lines)
    assert "archive_use_free_limit" in joined
    assert "archive_free_limit_exhaustion_behavior" in joined
    assert "archive_max_size_gb" in joined
    assert any(
        "archive_use_free_limit" in line and "= true" in line for line in active_lines
    )
    assert any(
        "archive_free_limit_exhaustion_behavior" in line and "AutoPause" in line
        for line in active_lines
    )
    assert any(
        "archive_max_size_gb" in line and "32" in line for line in active_lines
    )
    assert any(
        line.startswith("use_free_limit") and "local.archive_use_free_limit" in line
        for line in active_lines
    )
    assert any(
        line.startswith("free_limit_exhaustion_behavior")
        and "local.archive_free_limit_exhaustion_behavior" in line
        for line in active_lines
    )
    assert any(
        line.startswith("max_size_gb") and "local.archive_max_size_gb" in line
        for line in active_lines
    )
    normalized = " ".join(joined.split())
    assert 'sku_name = "GP_S_Gen5_2"' in normalized
    # lifecycle preconditions must refuse plans that drop Free Offer guardrails (#186).
    assert "lifecycle" in joined
    assert "precondition" in joined
    assert "local.archive_use_free_limit == true" in joined
    assert 'local.archive_free_limit_exhaustion_behavior == "AutoPause"' in joined
    assert "local.archive_max_size_gb <= 32" in joined
