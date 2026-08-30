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
import subprocess
import sys
from typing import Any


REQUIRED_USE_FREE_LIMIT = True
REQUIRED_EXHAUSTION = "AutoPause"
MAX_SIZE_GB = 32.0
# Expected Free Offer serverless shape for this project (#186).
REQUIRED_AUTO_PAUSE_DELAY_MINUTES = 60
REQUIRED_MIN_CAPACITY = 0.5


def _az_sql_db_show(*, resource_group: str, server: str, database: str) -> dict[str, Any]:
    cmd = [
        "az",
        "sql",
        "db",
        "show",
        "--resource-group",
        resource_group,
        "--server",
        server,
        "--name",
        database,
        "-o",
        "json",
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
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


def verify_free_offer_payload(payload: dict[str, Any]) -> list[str]:
    """Return human-readable failure reasons (empty means PASS)."""
    failures: list[str] = []
    use_free = payload.get("useFreeLimit")
    exhaustion = payload.get("freeLimitExhaustionBehavior")
    sku_payload = payload.get("currentSku")
    if not isinstance(sku_payload, dict):
        sku_payload = payload.get("sku") if isinstance(payload.get("sku"), dict) else {}
    sku_name = str(sku_payload.get("name") or "")
    capacity = sku_payload.get("capacity")
    auto_pause = payload.get("autoPauseDelay")
    min_capacity = payload.get("minCapacity")
    max_size_gb = _gb_from_bytes(payload.get("maxSizeBytes"))
    if max_size_gb is None and payload.get("maxSizeGb") is not None:
        try:
            max_size_gb = float(payload["maxSizeGb"])
        except (TypeError, ValueError):
            max_size_gb = None

    if use_free is not True:
        failures.append(
            f"useFreeLimit must be true (live value={use_free!r}). "
            "null/false means the Free Offer is NOT active — the DB may be billable. "
            "Tags like cost-tier=azure-sql-free-offer do not enroll Free Offer. "
            "Existing paid DBs often cannot be converted in place; create a new Free Offer database."
        )
    if exhaustion != REQUIRED_EXHAUSTION:
        failures.append(
            f"freeLimitExhaustionBehavior must be {REQUIRED_EXHAUSTION!r} "
            f"(live value={exhaustion!r})"
        )
    # Serverless SKUs are GP_S_* (General Purpose Serverless).
    if "GP_S" not in sku_name.upper() and "SERVERLESS" not in sku_name.upper():
        # Some payloads nest differently; also accept computeModel when present.
        compute_model = str(payload.get("currentServiceObjectiveName") or "")
        if "GP_S" not in compute_model.upper():
            failures.append(
                f"Expected serverless compute (GP_S_*), live sku/objective={sku_name or compute_model!r}"
            )
    if max_size_gb is not None and max_size_gb > MAX_SIZE_GB + 0.01:
        failures.append(f"max size {max_size_gb:.2f} GiB exceeds Free Offer limit {MAX_SIZE_GB}")
    try:
        if auto_pause is None:
            failures.append(
                "autoPauseDelay is missing; expected "
                f"{REQUIRED_AUTO_PAUSE_DELAY_MINUTES} for serverless AutoPause"
            )
        else:
            pause_minutes = int(auto_pause)
            if pause_minutes < 0:
                failures.append(
                    f"autoPauseDelay={auto_pause!r} disables pause; "
                    "serverless will stay billed/awake"
                )
            elif pause_minutes != REQUIRED_AUTO_PAUSE_DELAY_MINUTES:
                failures.append(
                    f"autoPauseDelay must be {REQUIRED_AUTO_PAUSE_DELAY_MINUTES} "
                    f"(live value={auto_pause!r})"
                )
    except (TypeError, ValueError):
        failures.append(f"autoPauseDelay is not an int (live value={auto_pause!r})")
    try:
        if min_capacity is None:
            failures.append(
                f"minCapacity is missing; expected {REQUIRED_MIN_CAPACITY} for Free Offer serverless"
            )
        elif abs(float(min_capacity) - REQUIRED_MIN_CAPACITY) > 0.001:
            failures.append(
                f"minCapacity must be {REQUIRED_MIN_CAPACITY} (live value={min_capacity!r})"
            )
    except (TypeError, ValueError):
        failures.append(f"minCapacity is not numeric (live value={min_capacity!r})")
    _ = capacity
    return failures


def _append_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


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
        payload = _az_sql_db_show(
            resource_group=args.resource_group,
            server=args.server,
            database=args.database,
        )
    except RuntimeError as exc:
        print(f"Free Offer verification failed to query Azure: {exc}", file=sys.stderr)
        if args.ci_summary:
            _append_summary(
                [
                    "## Azure SQL Free Offer verification",
                    "",
                    "Free Offer verification: FAIL (Azure query error)",
                    "",
                ]
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
                [
                    "## Azure SQL Free Offer verification",
                    "",
                    "Free Offer verification: FAIL",
                    "",
                    *[f"- {reason}" for reason in failures],
                    "",
                ]
            )
        return 1

    print("Free Offer verification: PASS")
    if args.ci_summary:
        _append_summary(
            [
                "## Azure SQL Free Offer verification",
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
