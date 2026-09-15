"""Lambda stale-heartbeat checker without live AWS (#216)."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
LAMBDA_PATH = ROOT / "infra" / "aws-health-monitor" / "lambda" / "stale_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("aws_stale_check", LAMBDA_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = _load_module()


def test_recent_heartbeat_classification() -> None:
    now = datetime(2026, 9, 15, 4, 30, tzinfo=timezone.utc)
    latest = {
        "service_name": "stock-ticker-backend",
        "checked_at": "2026-09-15T04:15:00Z",
        "status": "healthy",
        "http_status": 200,
    }
    result = sc.classify_heartbeat(latest, now=now, stale_after_seconds=1800)
    assert result["classification"] == "recent"
    assert result["age_seconds"] == 15 * 60
    assert result["heartbeat_status"] == "healthy"


def test_stale_heartbeat_classification() -> None:
    now = datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc)
    latest = {
        "checked_at": "2026-09-15T04:00:00Z",
        "status": "healthy",
    }
    result = sc.classify_heartbeat(latest, now=now, stale_after_seconds=1800)
    assert result["classification"] == "stale"
    assert result["age_seconds"] == 3600


def test_missing_heartbeat_classification() -> None:
    result = sc.classify_heartbeat(None, now=datetime.now(timezone.utc))
    assert result["classification"] == "missing"
    assert result["age_seconds"] is None
    assert result["checked_at"] is None


def test_boundary_exactly_stale_after_is_recent() -> None:
    now = datetime(2026, 9, 15, 4, 30, tzinfo=timezone.utc)
    latest = {"checked_at": "2026-09-15T04:00:00Z", "status": "unhealthy"}
    result = sc.classify_heartbeat(latest, now=now, stale_after_seconds=1800)
    assert result["classification"] == "recent"
    assert result["age_seconds"] == 1800


def test_missing_checked_at_is_missing() -> None:
    result = sc.classify_heartbeat(
        {"status": "healthy", "http_status": 200},
        now=datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc),
    )
    assert result["classification"] == "missing"
    assert result["error_type"] == "missing_checked_at"
    assert result["heartbeat_status"] == "healthy"


def test_recent_unhealthy_heartbeat_is_still_recent() -> None:
    now = datetime(2026, 9, 15, 4, 10, tzinfo=timezone.utc)
    latest = {
        "checked_at": "2026-09-15T04:00:00Z",
        "status": "unhealthy",
        "error_type": "http_status",
    }
    result = sc.classify_heartbeat(latest, now=now, stale_after_seconds=1800)
    assert result["classification"] == "recent"
    assert result["heartbeat_status"] == "unhealthy"


def test_run_stale_check_uses_query_injection() -> None:
    now = datetime(2026, 9, 15, 4, 20, tzinfo=timezone.utc)

    def query_latest(table_name: str, service_name: str):
        assert table_name == "heartbeats"
        assert service_name == "stock-ticker-backend"
        return {
            "checked_at": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "healthy",
        }

    result = sc.run_stale_check(
        table_name="heartbeats",
        now=now,
        query_latest=query_latest,
    )
    assert result["classification"] == "recent"


def test_compact_log_line_is_one_line() -> None:
    line = sc.compact_log_line(
        {
            "classification": "stale",
            "age_seconds": 4000,
            "heartbeat_status": "healthy",
            "checked_at": "2026-09-15T03:00:00Z",
            "error_type": None,
        }
    )
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["event"] == "aws_health_monitor.stale_check"
    assert payload["classification"] == "stale"


def test_lambda_source_has_no_home_url_or_http_client() -> None:
    source = LAMBDA_PATH.read_text(encoding="utf-8")
    assert "HEALTH_URL" not in source
    assert "health_url" not in source
    assert "urllib" not in source
    assert "import requests" not in source
    assert "http_get" not in source
    before_fetch, after_fetch = source.split("def fetch_latest_heartbeat", 1)
    assert "import boto3" not in before_fetch
    assert "import boto3" in after_fetch


def test_handler_queries_via_injected_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTable:
        def query(self, **kwargs):
            assert kwargs["Limit"] == 1
            assert kwargs["ScanIndexForward"] is False
            return {
                "Items": [
                    {
                        "service_name": "stock-ticker-backend",
                        "checked_at": "2026-09-15T04:00:00Z",
                        "status": "healthy",
                    }
                ]
            }

    class FakeResource:
        def Table(self, name):  # noqa: N802
            assert name == "heartbeats"
            return FakeTable()

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(resource=lambda _svc: FakeResource()),
    )
    monkeypatch.setenv("TABLE_NAME", "heartbeats")
    monkeypatch.setenv("STALE_AFTER_SECONDS", "1800")
    monkeypatch.setattr(
        sc,
        "utc_now",
        lambda: datetime(2026, 9, 15, 4, 10, tzinfo=timezone.utc),
    )

    result = sc.handler({}, None)
    assert result["classification"] == "recent"
    assert result["heartbeat_status"] == "healthy"
