# Edit infra/aws-health-monitor/heartbeat/send_heartbeat.py, then copy to
# k8s/aws-health-heartbeat/send_heartbeat.py (ConfigMap source). Keep identical
# (enforced by backend/tests/unit/infra/test_aws_health_monitor_heartbeat.py).
"""Home-node heartbeat sender for AWS DynamoDB (issue #216).

Calls local FastAPI /health, then PutItem over outbound HTTPS.
Stdlib only (urllib + SigV4) so the CronJob needs no boto3 layer.
Never stores the health response body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

SERVICE_NAME = "stock-ticker-backend"
HTTP_TIMEOUT_SECONDS = 3.0
MESSAGE_MAX_LEN = 180
DEFAULT_TTL_DAYS = 7
USER_AGENT = "stock-ticker-aws-health-heartbeat/1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def classify_health_payload(http_status: int, body_text: str) -> dict[str, Any]:
    """Return status/error fields from an HTTP response without storing the body."""
    if http_status != 200:
        return {
            "status": "unhealthy",
            "http_status": http_status,
            "error_type": "http_status",
            "message": f"HTTP {http_status}",
        }
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        return {
            "status": "unhealthy",
            "http_status": http_status,
            "error_type": "invalid_json",
            "message": "Response was not JSON",
        }
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        top = payload.get("status") if isinstance(payload, dict) else type(payload).__name__
        return {
            "status": "unhealthy",
            "http_status": http_status,
            "error_type": "unexpected_status",
            "message": f"JSON status {top!r} is not 'ok'",
        }
    return {
        "status": "healthy",
        "http_status": http_status,
        "error_type": None,
        "message": None,
    }


def http_get(url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(4096).decode("utf-8", errors="replace")
        return int(response.status), body


def probe_health(
    url: str,
    *,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    getter: Callable[[str, float], tuple[int, str]] | None = None,
) -> dict[str, Any]:
    do_get = http_get if getter is None else getter
    try:
        http_status, body = do_get(url, timeout)
    except TimeoutError:
        return {
            "status": "unhealthy",
            "http_status": None,
            "error_type": "timeout",
            "message": "Health request timed out",
        }
    except urllib.error.HTTPError as exc:
        return classify_health_payload(int(exc.code), "")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return {
                "status": "unhealthy",
                "http_status": None,
                "error_type": "timeout",
                "message": "Health request timed out",
            }
        return {
            "status": "unhealthy",
            "http_status": None,
            "error_type": "network",
            "message": _bound_message(str(reason) or "network error"),
        }
    except Exception as exc:  # noqa: BLE001 — always emit a heartbeat record
        return {
            "status": "unhealthy",
            "http_status": None,
            "error_type": "unexpected",
            "message": _bound_message(f"{type(exc).__name__}"),
        }
    return classify_health_payload(http_status, body)


def build_record(
    *,
    service_name: str,
    checked_at: datetime,
    latency_ms: int,
    probe: dict[str, Any],
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> dict[str, Any]:
    checked_iso = iso_utc(checked_at)
    expires = checked_at + timedelta(days=ttl_days)
    return {
        "service_name": service_name,
        "checked_at": checked_iso,
        "check_id": f"{service_name}#{checked_iso}",
        "status": probe["status"],
        "http_status": probe.get("http_status"),
        "latency_ms": int(latency_ms),
        "error_type": probe.get("error_type"),
        "message": _bound_message(probe.get("message")),
        "expires_at": int(expires.timestamp()),
    }


def compact_record(record: dict[str, Any]) -> dict[str, Any]:
    """Omit None fields (DynamoDB rejects null attribute values)."""
    return {key: value for key, value in record.items() if value is not None}


def run_heartbeat(
    health_url: str,
    *,
    service_name: str = SERVICE_NAME,
    ttl_days: int = DEFAULT_TTL_DAYS,
    now: datetime | None = None,
    getter: Callable[[str, float], tuple[int, str]] | None = None,
    put_item: Callable[[dict[str, Any]], None] | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    started = utc_now()
    checked_at = now or started
    probe = probe_health(health_url, timeout=timeout, getter=getter)
    latency_ms = max(0, int((utc_now() - started).total_seconds() * 1000))
    record = build_record(
        service_name=service_name,
        checked_at=checked_at,
        latency_ms=latency_ms,
        probe=probe,
        ttl_days=ttl_days,
    )
    item = compact_record(record)
    if put_item is not None:
        put_item(item)
    return record


def to_dynamodb_item(item: dict[str, Any]) -> dict[str, dict[str, str]]:
    encoded: dict[str, dict[str, str]] = {}
    for key, value in item.items():
        if isinstance(value, bool):
            encoded[key] = {"BOOL": str(value).lower()}
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            encoded[key] = {"N": str(value)}
        else:
            encoded[key] = {"S": str(value)}
    return encoded


def put_dynamodb_item(
    *,
    region: str,
    table_name: str,
    item: dict[str, Any],
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None = None,
) -> None:
    """PutItem via signed HTTPS. No boto3 required on the home node."""
    host = f"dynamodb.{region}.amazonaws.com"
    amz_target = "DynamoDB_20120810.PutItem"
    payload = json.dumps(
        {"TableName": table_name, "Item": to_dynamodb_item(item)},
        separators=(",", ":"),
    )
    headers = _sigv4_headers(
        method="POST",
        host=host,
        region=region,
        service="dynamodb",
        payload=payload,
        amz_target=amz_target,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )
    request = urllib.request.Request(
        f"https://{host}/",
        data=payload.encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"DynamoDB PutItem failed HTTP {exc.code}: {body}") from exc


def main(argv: list[str] | None = None) -> int:
    _ = argv
    health_url = os.environ.get(
        "HEALTH_URL",
        "http://stock-ticker-backend:8000/health",
    )
    table_name = os.environ["TABLE_NAME"]
    region = os.environ["AWS_REGION"]
    access_key_id = os.environ["AWS_ACCESS_KEY_ID"]
    secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"]
    session_token = os.environ.get("AWS_SESSION_TOKEN") or None
    service_name = os.environ.get("SERVICE_NAME", SERVICE_NAME)
    ttl_days = int(os.environ.get("TTL_DAYS", str(DEFAULT_TTL_DAYS)))

    def put_item(item: dict[str, Any]) -> None:
        put_dynamodb_item(
            region=region,
            table_name=table_name,
            item=item,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

    record = run_heartbeat(
        health_url,
        service_name=service_name,
        ttl_days=ttl_days,
        put_item=put_item,
    )
    # One compact line for CronJob logs; never the health body or secrets.
    print(
        json.dumps(
            {
                "event": "aws_health_monitor.heartbeat",
                "status": record["status"],
                "http_status": record["http_status"],
                "latency_ms": record["latency_ms"],
                "error_type": record["error_type"],
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0


def _bound_message(message: object) -> str | None:
    if message is None:
        return None
    text = str(message).strip()
    if not text:
        return None
    if len(text) > MESSAGE_MAX_LEN:
        return text[: MESSAGE_MAX_LEN - 3] + "..."
    return text


def _sigv4_headers(
    *,
    method: str,
    host: str,
    region: str,
    service: str,
    payload: str,
    amz_target: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None,
) -> dict[str, str]:
    now = utc_now()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    # Header names must be sorted lexicographically in both the canonical
    # request and SignedHeaders (required when x-amz-security-token is present).
    signed_header_map = {
        "content-type": "application/x-amz-json-1.0",
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-target": amz_target,
    }
    if session_token:
        signed_header_map["x-amz-security-token"] = session_token
    signed_header_names = sorted(signed_header_map)
    canonical_headers = "".join(
        f"{name}:{signed_header_map[name]}\n" for name in signed_header_names
    )
    signed_headers = ";".join(signed_header_names)
    canonical_request = "\n".join(
        [
            method,
            "/",
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _signing_key(secret_access_key, date_stamp, region, service)
    signature = hmac.new(
        signing_key,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    headers = {
        "Content-Type": "application/x-amz-json-1.0",
        "Host": host,
        "X-Amz-Date": amz_date,
        "X-Amz-Target": amz_target,
        "Authorization": authorization,
        "User-Agent": USER_AGENT,
    }
    if session_token:
        headers["X-Amz-Security-Token"] = session_token
    return headers


def _signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


if __name__ == "__main__":
    sys.exit(main())
