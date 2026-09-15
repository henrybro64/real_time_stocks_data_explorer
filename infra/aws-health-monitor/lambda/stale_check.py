"""AWS Lambda stale-heartbeat checker (issue #216).

Reads the newest DynamoDB heartbeat written by the home node.
Does not call the home node or any public /health URL.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

SERVICE_NAME = "stock-ticker-backend"
DEFAULT_STALE_AFTER_SECONDS = 30 * 60


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_checked_at(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def classify_heartbeat(
    latest: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Classify newest heartbeat as recent, stale, or missing."""
    checked = now or utc_now()
    if not latest:
        return {
            "classification": "missing",
            "age_seconds": None,
            "heartbeat_status": None,
            "checked_at": None,
            "http_status": None,
            "error_type": None,
        }

    checked_at_raw = latest.get("checked_at")
    if not checked_at_raw:
        return {
            "classification": "missing",
            "age_seconds": None,
            "heartbeat_status": latest.get("status"),
            "checked_at": None,
            "http_status": latest.get("http_status"),
            "error_type": "missing_checked_at",
        }

    checked_at = parse_checked_at(str(checked_at_raw))
    age_seconds = max(0, int((checked - checked_at).total_seconds()))
    classification = "recent" if age_seconds <= stale_after_seconds else "stale"
    return {
        "classification": classification,
        "age_seconds": age_seconds,
        "heartbeat_status": latest.get("status"),
        "checked_at": str(checked_at_raw),
        "http_status": latest.get("http_status"),
        "error_type": latest.get("error_type"),
    }


def compact_log_line(result: dict[str, Any]) -> str:
    return json.dumps(
        {
            "event": "aws_health_monitor.stale_check",
            "classification": result["classification"],
            "age_seconds": result["age_seconds"],
            "heartbeat_status": result["heartbeat_status"],
            "checked_at": result["checked_at"],
            "error_type": result["error_type"],
        },
        separators=(",", ":"),
    )


def fetch_latest_heartbeat(
    table_name: str,
    service_name: str,
    *,
    query_latest: Callable[[str, str], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    if query_latest is not None:
        return query_latest(table_name, service_name)

    import boto3  # type: ignore[import-not-found]  # Lambda runtime

    table = boto3.resource("dynamodb").Table(table_name)
    response = table.query(
        KeyConditionExpression="service_name = :s",
        ExpressionAttributeValues={":s": service_name},
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items") or []
    return items[0] if items else None


def run_stale_check(
    *,
    table_name: str,
    service_name: str = SERVICE_NAME,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    now: datetime | None = None,
    query_latest: Callable[[str, str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    latest = fetch_latest_heartbeat(
        table_name,
        service_name,
        query_latest=query_latest,
    )
    return classify_heartbeat(
        latest,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    _ = event, context
    table_name = os.environ["TABLE_NAME"]
    service_name = os.environ.get("SERVICE_NAME", SERVICE_NAME)
    stale_after_seconds = int(
        os.environ.get("STALE_AFTER_SECONDS", str(DEFAULT_STALE_AFTER_SECONDS))
    )
    result = run_stale_check(
        table_name=table_name,
        service_name=service_name,
        stale_after_seconds=stale_after_seconds,
    )
    print(compact_log_line(result))
    return {
        "classification": result["classification"],
        "age_seconds": result["age_seconds"],
        "heartbeat_status": result["heartbeat_status"],
    }
