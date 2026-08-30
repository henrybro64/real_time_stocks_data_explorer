#!/usr/bin/env python3
"""Verify a live Azure SQL database is enrolled in the Free Offer (#186).

Read-only. Uses Azure CLI (`az sql db show`). Does not print passwords or
connection strings.

Exit codes:
  0 — Free Offer + AutoPause confirmed
  1 — live resource is missing Free Offer / unexpected cost shape (FAIL loudly)
  2 — usage / CLI / argument error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any


REQUIRED_USE_FREE_LIMIT = True
REQUIRED_EXHAUSTION = "AutoPause"
MAX_SIZE_GB = 32.0
# Expected Free Offer serverless shape for this project (#186).
REQUIRED_AUTO_PAUSE_DELAY_MINUTES = 60
REQUIRED_MIN_CAPACITY = 0.5
# Azure resource names: letters, digits, underscore, period, hyphen only.
_AZURE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_CI_SUMMARY_HEADING = "## Azure SQL Free Offer verification"


def _require_azure_name(value: str, *, label: str) -> str:
    """Reject shell metacharacters before any OS command invocation."""
    cleaned = value.strip()
    if not cleaned or not _AZURE_NAME_RE.fullmatch(cleaned):
        raise ValueError(
            f"Invalid {label} {value!r}: expected Azure name matching "
            f"{_AZURE_NAME_RE.pattern}"
        )
    return cleaned


def _az_sql_db_show(*, resource_group: str, server: str, database: str) -> dict[str, Any]:
    safe_rg = _require_azure_name(resource_group, label="resource-group")
    safe_server = _require_azure_name(server, label="server")
    safe_database = _require_azure_name(database, label="database")
    cmd = [
        "az",
        "sql",
        "db",
        "show",
        "--resource-group",
        safe_rg,
        "--server",
        safe_server,
        "--name",
        safe_database,
        "-o",
        "json",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - args are allowlist-validated above
            cmd,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI (`az`) is not installed or not on PATH") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"az sql db show failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("az sql db show returned non-JSON output") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("az sql db show returned unexpected JSON shape")
    return payload


def _gb_from_bytes(value: object) -> float | None:
    if value is None:
        return None
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    # Azure may report maxSizeBytes; convert when clearly byte-sized.
    if raw > 10_000:
        return raw / (1024.0**3)
    return raw


def _sku_name(payload: dict[str, Any]) -> str:
    sku_payload = payload.get("currentSku")
    if not isinstance(sku_payload, dict):
        sku_payload = payload.get("sku") if isinstance(payload.get("sku"), dict) else {}
    return str(sku_payload.get("name") or "")


def _max_size_gb(payload: dict[str, Any]) -> float | None:
    max_size_gb = _gb_from_bytes(payload.get("maxSizeBytes"))
    if max_size_gb is not None:
        return max_size_gb
    if payload.get("maxSizeGb") is None:
        return None
    try:
        return float(payload["maxSizeGb"])
    except (TypeError, ValueError):
        return None


def _check_free_limit(use_free: object) -> str | None:
    if use_free is True:
        return None
    return (
        f"useFreeLimit must be true (live value={use_free!r}). "
        "null/false means the Free Offer is NOT active — the DB may be billable. "
        "Tags like cost-tier=azure-sql-free-offer do not enroll Free Offer. "
        "Existing paid DBs often cannot be converted in place; create a new Free Offer database."
    )


def _check_exhaustion(exhaustion: object) -> str | None:
    if exhaustion == REQUIRED_EXHAUSTION:
        return None
    return (
        f"freeLimitExhaustionBehavior must be {REQUIRED_EXHAUSTION!r} "
        f"(live value={exhaustion!r})"
    )


def _check_serverless(sku_name: str, payload: dict[str, Any]) -> str | None:
    if "GP_S" in sku_name.upper() or "SERVERLESS" in sku_name.upper():
        return None
    compute_model = str(payload.get("currentServiceObjectiveName") or "")
    if "GP_S" in compute_model.upper():
        return None
    return (
        f"Expected serverless compute (GP_S_*), "
        f"live sku/objective={sku_name or compute_model!r}"
    )


def _check_max_size(max_size_gb: float | None) -> str | None:
    if max_size_gb is None or max_size_gb <= MAX_SIZE_GB + 0.01:
        return None
    return f"max size {max_size_gb:.2f} GiB exceeds Free Offer limit {MAX_SIZE_GB}"


def _check_auto_pause(auto_pause: object) -> str | None:
    if auto_pause is None:
        return (
            "autoPauseDelay is missing; expected "
            f"{REQUIRED_AUTO_PAUSE_DELAY_MINUTES} for serverless AutoPause"
        )
    try:
        pause_minutes = int(auto_pause)
    except (TypeError, ValueError):
        return f"autoPauseDelay is not an int (live value={auto_pause!r})"
    if pause_minutes < 0:
        return (
            f"autoPauseDelay={auto_pause!r} disables pause; "
            "serverless will stay billed/awake"
        )
    if pause_minutes != REQUIRED_AUTO_PAUSE_DELAY_MINUTES:
        return (
            f"autoPauseDelay must be {REQUIRED_AUTO_PAUSE_DELAY_MINUTES} "
            f"(live value={auto_pause!r})"
        )
    return None


def _check_min_capacity(min_capacity: object) -> str | None:
    if min_capacity is None:
        return (
            f"minCapacity is missing; expected {REQUIRED_MIN_CAPACITY} "
            "for Free Offer serverless"
        )
    try:
        if abs(float(min_capacity) - REQUIRED_MIN_CAPACITY) > 0.001:
            return (
                f"minCapacity must be {REQUIRED_MIN_CAPACITY} "
                f"(live value={min_capacity!r})"
            )
    except (TypeError, ValueError):
        return f"minCapacity is not numeric (live value={min_capacity!r})"
    return None


def verify_free_offer_payload(payload: dict[str, Any]) -> list[str]:
    """Return human-readable failure reasons (empty means PASS)."""
    checks = [
        _check_free_limit(payload.get("useFreeLimit")),
        _check_exhaustion(payload.get("freeLimitExhaustionBehavior")),
        _check_serverless(_sku_name(payload), payload),
        _check_max_size(_max_size_gb(payload)),
        _check_auto_pause(payload.get("autoPauseDelay")),
        _check_min_capacity(payload.get("minCapacity")),
    ]
    return [reason for reason in checks if reason is not None]


def _append_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _ci_fail_summary(*extra_lines: str) -> list[str]:
    return [_CI_SUMMARY_HEADING, "", *extra_lines, ""]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resource-group",
        default=os.environ.get("AZURE_RESOURCE_GROUP", "").strip(),
        help="Azure resource group (or env AZURE_RESOURCE_GROUP)",
    )
    parser.add_argument(
        "--server",
        default=os.environ.get("AZURE_SQL_SERVER_NAME", "").strip(),
        help="SQL server name without .database.windows.net (or env AZURE_SQL_SERVER_NAME)",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("AZURE_SQL_DATABASE_NAME", "rankings_archive").strip(),
        help="Database name (default rankings_archive or env AZURE_SQL_DATABASE_NAME)",
    )
    parser.add_argument(
        "--ci-summary",
        action="store_true",
        help="Append PASS/FAIL lines to $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args(argv)

    if not args.resource_group or not args.server or not args.database:
        print(
            "usage: require --resource-group/--server/--database "
            "(or AZURE_RESOURCE_GROUP / AZURE_SQL_SERVER_NAME)",
            file=sys.stderr,
        )
        return 2

    try:
        resource_group = _require_azure_name(args.resource_group, label="resource-group")
        server = _require_azure_name(args.server, label="server")
        database = _require_azure_name(args.database, label="database")
        payload = _az_sql_db_show(
            resource_group=resource_group,
            server=server,
            database=database,
        )
    except ValueError as exc:
        print(f"Free Offer verification argument error: {exc}", file=sys.stderr)
        if args.ci_summary:
            _append_summary(_ci_fail_summary("Free Offer verification: FAIL (bad arguments)"))
        return 2
    except RuntimeError as exc:
        print(f"Free Offer verification failed to query Azure: {exc}", file=sys.stderr)
        if args.ci_summary:
            _append_summary(
                _ci_fail_summary("Free Offer verification: FAIL (Azure query error)")
            )
        return 2

    # Never dump the full payload (may include secondary endpoints); print only
    # the cost-safety fields needed for operators.
    safe_view = {
        "name": payload.get("name"),
        "useFreeLimit": payload.get("useFreeLimit"),
        "freeLimitExhaustionBehavior": payload.get("freeLimitExhaustionBehavior"),
        "autoPauseDelay": payload.get("autoPauseDelay"),
        "minCapacity": payload.get("minCapacity"),
        "maxSizeBytes": payload.get("maxSizeBytes"),
        "currentSku": payload.get("currentSku"),
    }
    print("Live Azure SQL cost-safety fields:")
    print(json.dumps(safe_view, indent=2, default=str))

    failures = verify_free_offer_payload(payload)
    if failures:
        print("Free Offer verification: FAIL — database may be billable", file=sys.stderr)
        for reason in failures:
            print(f"- {reason}", file=sys.stderr)
        if args.ci_summary:
            _append_summary(
                _ci_fail_summary(
                    "Free Offer verification: FAIL",
                    "",
                    *[f"- {reason}" for reason in failures],
                )
            )
        return 1

    print("Free Offer verification: PASS")
    if args.ci_summary:
        _append_summary(
            [
                _CI_SUMMARY_HEADING,
                "",
                "Free Offer verification: PASS",
                "",
                "- useFreeLimit: true",
                "- freeLimitExhaustionBehavior: AutoPause",
                f"- autoPauseDelay: {safe_view.get('autoPauseDelay')}",
                f"- minCapacity: {safe_view.get('minCapacity')}",
                "",
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
