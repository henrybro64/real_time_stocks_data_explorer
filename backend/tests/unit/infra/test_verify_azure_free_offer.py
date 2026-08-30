"""Unit tests for Free Offer verification helper (#186)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parents[4]
    / "scripts"
    / "verify_azure_rankings_archive_free_offer.py"
)


def _load_verify_module():
    spec = importlib.util.spec_from_file_location(
        "verify_azure_rankings_archive_free_offer",
        _SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_free_offer_payload_accepts_enrolled_serverless() -> None:
    verify = _load_verify_module()
    failures = verify.verify_free_offer_payload(
        {
            "useFreeLimit": True,
            "freeLimitExhaustionBehavior": "AutoPause",
            "autoPauseDelay": 60,
            "minCapacity": 0.5,
            "maxSizeBytes": 32 * 1024**3,
            "currentSku": {"name": "GP_S_Gen5", "capacity": 2},
        }
    )
    assert failures == []


def test_verify_free_offer_payload_fails_when_free_limit_null() -> None:
    verify = _load_verify_module()
    failures = verify.verify_free_offer_payload(
        {
            "useFreeLimit": None,
            "freeLimitExhaustionBehavior": None,
            "currentSku": {"name": "GP_S_Gen5", "capacity": 2},
            "maxSizeBytes": 32 * 1024**3,
        }
    )
    assert failures
    joined = "\n".join(failures)
    assert "useFreeLimit" in joined
    assert "NOT active" in joined
    assert "billable" in joined.lower()


def test_verify_free_offer_payload_fails_when_auto_pause_disabled() -> None:
    verify = _load_verify_module()
    failures = verify.verify_free_offer_payload(
        {
            "useFreeLimit": True,
            "freeLimitExhaustionBehavior": "AutoPause",
            "autoPauseDelay": -1,
            "minCapacity": 0.5,
            "currentSku": {"name": "GP_S_Gen5", "capacity": 2},
            "maxSizeBytes": 32 * 1024**3,
        }
    )
    assert failures
    assert any("autoPauseDelay" in item for item in failures)


def test_verify_free_offer_payload_fails_when_min_capacity_wrong() -> None:
    verify = _load_verify_module()
    failures = verify.verify_free_offer_payload(
        {
            "useFreeLimit": True,
            "freeLimitExhaustionBehavior": "AutoPause",
            "autoPauseDelay": 60,
            "minCapacity": 2.0,
            "currentSku": {"name": "GP_S_Gen5", "capacity": 2},
            "maxSizeBytes": 32 * 1024**3,
        }
    )
    assert failures
    assert any("minCapacity" in item for item in failures)


def test_verify_free_offer_payload_fails_when_auto_pause_not_sixty() -> None:
    verify = _load_verify_module()
    failures = verify.verify_free_offer_payload(
        {
            "useFreeLimit": True,
            "freeLimitExhaustionBehavior": "AutoPause",
            "autoPauseDelay": 120,
            "minCapacity": 0.5,
            "currentSku": {"name": "GP_S_Gen5", "capacity": 2},
            "maxSizeBytes": 32 * 1024**3,
        }
    )
    assert failures
    assert any("autoPauseDelay" in item for item in failures)


def test_require_azure_name_rejects_shell_metacharacters() -> None:
    verify = _load_verify_module()
    try:
        verify._require_azure_name("rg; rm -rf /", label="resource-group")
    except ValueError as exc:
        assert "Invalid resource-group" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert verify._require_azure_name("stock-ticker-rg", label="resource-group") == (
        "stock-ticker-rg"
    )


def test_verify_script_defaults_to_rankings_archive_free() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert (
        'default=os.environ.get("AZURE_SQL_DATABASE_NAME", "rankings_archive_free")'
        in text
    )
    assert (
        'default=os.environ.get("AZURE_SQL_DATABASE_NAME", "rankings_archive")'
        not in text
    )
