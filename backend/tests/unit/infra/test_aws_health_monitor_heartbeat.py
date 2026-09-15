"""Home-node heartbeat sender logic without live AWS (#216)."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest


ROOT = Path(__file__).resolve().parents[4]
HEARTBEAT_PATH = ROOT / "infra" / "aws-health-monitor" / "heartbeat" / "send_heartbeat.py"
K8S_COPY_PATH = ROOT / "k8s" / "aws-health-heartbeat" / "send_heartbeat.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("aws_health_heartbeat", HEARTBEAT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hb = _load_module()


def _ok_getter(_url: str, _timeout: float) -> tuple[int, str]:
    return 200, json.dumps({"status": "ok", "quote_cache": {"configured": True}})


def test_k8s_script_copy_matches_infra_source() -> None:
    assert HEARTBEAT_PATH.is_file()
    assert K8S_COPY_PATH.is_file()
    assert HEARTBEAT_PATH.read_text(encoding="utf-8") == K8S_COPY_PATH.read_text(
        encoding="utf-8"
    )


def test_healthy_when_http_200_and_status_ok() -> None:
    written: list[dict] = []
    record = hb.run_heartbeat(
        "http://stock-ticker-backend:8000/health",
        getter=_ok_getter,
        put_item=written.append,
        now=datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc),
    )

    assert record["status"] == "healthy"
    assert record["http_status"] == 200
    assert record["error_type"] is None
    assert written[0]["service_name"] == "stock-ticker-backend"
    assert written[0]["checked_at"] == "2026-09-15T04:00:00Z"
    assert written[0]["check_id"] == "stock-ticker-backend#2026-09-15T04:00:00Z"
    assert "error_type" not in written[0]
    assert "message" not in written[0]
    assert "quote_cache" not in json.dumps(written[0])
    assert written[0]["expires_at"] == int(
        datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc).timestamp()
    ) + 7 * 24 * 3600


def test_unhealthy_on_non_200() -> None:
    def getter(_url: str, _timeout: float) -> tuple[int, str]:
        return 503, json.dumps({"status": "ok"})

    record = hb.run_heartbeat(
        "http://stock-ticker-backend:8000/health",
        getter=getter,
        put_item=lambda _item: None,
    )
    assert record["status"] == "unhealthy"
    assert record["http_status"] == 503
    assert record["error_type"] == "http_status"


def test_unhealthy_on_invalid_json() -> None:
    def getter(_url: str, _timeout: float) -> tuple[int, str]:
        return 200, "<html>nope</html>"

    record = hb.run_heartbeat("http://stock-ticker-backend:8000/health", getter=getter)
    assert record["status"] == "unhealthy"
    assert record["error_type"] == "invalid_json"


def test_unhealthy_when_json_status_not_ok() -> None:
    def getter(_url: str, _timeout: float) -> tuple[int, str]:
        return 200, json.dumps({"status": "degraded"})

    record = hb.run_heartbeat("http://stock-ticker-backend:8000/health", getter=getter)
    assert record["status"] == "unhealthy"
    assert record["error_type"] == "unexpected_status"


def test_timeout_from_urllib_urlerror() -> None:
    def getter(_url: str, _timeout: float) -> tuple[int, str]:
        raise URLError(TimeoutError("timed out"))

    record = hb.run_heartbeat("http://stock-ticker-backend:8000/health", getter=getter)
    assert record["status"] == "unhealthy"
    assert record["http_status"] is None
    assert record["error_type"] == "timeout"


def test_direct_timeout_error_is_unhealthy() -> None:
    def getter(_url: str, _timeout: float) -> tuple[int, str]:
        raise TimeoutError("timed out")

    record = hb.run_heartbeat("http://stock-ticker-backend:8000/health", getter=getter)
    assert record["status"] == "unhealthy"
    assert record["error_type"] == "timeout"


def test_network_failure_is_unhealthy() -> None:
    def getter(_url: str, _timeout: float) -> tuple[int, str]:
        raise URLError("Name or service not known")

    written: list[dict] = []
    record = hb.run_heartbeat(
        "http://stock-ticker-backend:8000/health",
        getter=getter,
        put_item=written.append,
    )
    assert record["status"] == "unhealthy"
    assert record["error_type"] == "network"
    assert "http_status" not in written[0]
    assert "Traceback" not in written[0]["message"]


def test_http_error_from_urllib_is_unhealthy() -> None:
    def getter(_url: str, _timeout: float) -> tuple[int, str]:
        raise HTTPError(
            "http://stock-ticker-backend:8000/health",
            502,
            "Bad Gateway",
            hdrs={},
            fp=None,
        )

    record = hb.run_heartbeat("http://stock-ticker-backend:8000/health", getter=getter)
    assert record["status"] == "unhealthy"
    assert record["http_status"] == 502
    assert record["error_type"] == "http_status"


def test_message_is_bounded() -> None:
    bounded = hb._bound_message("x" * 5000)
    assert bounded is not None
    assert len(bounded) <= hb.MESSAGE_MAX_LEN


def test_dynamodb_item_encoding_types() -> None:
    encoded = hb.to_dynamodb_item(
        {
            "service_name": "stock-ticker-backend",
            "latency_ms": 12,
            "expires_at": 1_700_000_000,
        }
    )
    assert encoded["service_name"] == {"S": "stock-ticker-backend"}
    assert encoded["latency_ms"] == {"N": "12"}
    assert encoded["expires_at"] == {"N": "1700000000"}


def test_sigv4_signed_headers_are_sorted_with_session_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hb,
        "utc_now",
        lambda: datetime(2026, 9, 15, 4, 0, 0, tzinfo=timezone.utc),
    )
    headers = hb._sigv4_headers(
        method="POST",
        host="dynamodb.us-east-1.amazonaws.com",
        region="us-east-1",
        service="dynamodb",
        payload="{}",
        amz_target="DynamoDB_20120810.PutItem",
        access_key_id="AKIATEST",
        secret_access_key="secret",
        session_token="session-token-value",
    )
    auth = headers["Authorization"]
    signed = auth.split("SignedHeaders=")[1].split(",")[0]
    assert (
        signed
        == "content-type;host;x-amz-date;x-amz-security-token;x-amz-target"
    )
    assert headers["X-Amz-Security-Token"] == "session-token-value"


def test_sigv4_signed_headers_without_session_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hb,
        "utc_now",
        lambda: datetime(2026, 9, 15, 4, 0, 0, tzinfo=timezone.utc),
    )
    headers = hb._sigv4_headers(
        method="POST",
        host="dynamodb.us-east-1.amazonaws.com",
        region="us-east-1",
        service="dynamodb",
        payload="{}",
        amz_target="DynamoDB_20120810.PutItem",
        access_key_id="AKIATEST",
        secret_access_key="secret",
        session_token=None,
    )
    auth = headers["Authorization"]
    signed = auth.split("SignedHeaders=")[1].split(",")[0]
    assert signed == "content-type;host;x-amz-date;x-amz-target"
    assert "X-Amz-Security-Token" not in headers


def test_main_skips_without_credentials(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("TABLE_NAME", raising=False)
    assert hb.main() == 0
    logged = json.loads(capsys.readouterr().out.strip())
    assert logged["status"] == "skipped"
    assert logged["reason"] == "missing_credentials"


def test_put_dynamodb_item_posts_signed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_urlopen(request, timeout=0):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = request.data.decode("utf-8")
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        assert timeout == 10
        return FakeResponse()

    monkeypatch.setattr(hb.urllib.request, "urlopen", fake_urlopen)
    hb.put_dynamodb_item(
        region="us-east-1",
        table_name="stock-ticker-health-heartbeats",
        item={
            "service_name": "stock-ticker-backend",
            "checked_at": "2026-09-15T04:00:00Z",
            "status": "healthy",
            "latency_ms": 12,
            "expires_at": 1_700_000_000,
        },
        access_key_id="AKIATEST",
        secret_access_key="secret",
    )
    assert captured["url"] == "https://dynamodb.us-east-1.amazonaws.com/"
    assert captured["method"] == "POST"
    payload = json.loads(captured["body"])
    assert payload["TableName"] == "stock-ticker-health-heartbeats"
    assert payload["Item"]["service_name"] == {"S": "stock-ticker-backend"}
    assert payload["Item"]["latency_ms"] == {"N": "12"}
    assert payload["Item"]["expires_at"] == {"N": "1700000000"}
    headers = captured["headers"]
    assert headers["x-amz-target"] == "DynamoDB_20120810.PutItem"
    assert "authorization" in headers


def test_put_item_failure_propagates() -> None:
    def boom(_item: dict) -> None:
        raise RuntimeError("DynamoDB PutItem failed HTTP 400: boom")

    with pytest.raises(RuntimeError, match="PutItem failed"):
        hb.run_heartbeat(
            "http://stock-ticker-backend:8000/health",
            getter=_ok_getter,
            put_item=boom,
        )


def test_script_is_stdlib_only() -> None:
    source = HEARTBEAT_PATH.read_text(encoding="utf-8")
    assert "import boto3" not in source
    assert "import requests" not in source
    assert "import httpx" not in source


def test_backend_health_contract_still_requires_status_ok() -> None:
    health_test = ROOT / "backend" / "tests" / "integration" / "api" / "test_health_api.py"
    text = health_test.read_text(encoding="utf-8")
    assert 'client.get("/health")' in text
    assert 'payload["status"] == "ok"' in text
