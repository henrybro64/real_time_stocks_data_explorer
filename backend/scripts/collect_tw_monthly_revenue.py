#!/usr/bin/env python3
"""Operator-only bounded collection of latest official TW monthly revenue (#362 M1).

This script is intentionally NOT scheduled and must not be wired into browser
request paths. Production activation remains unauthorized until an owner
approves a bounded run.

Example (local disposable DB only):

  cd backend
  DATABASE_URL=postgresql+psycopg://... \\
    .venv/bin/python -m scripts.collect_tw_monthly_revenue

Exit codes:
  0 — every board accepted or not_modified
  2 — one or more boards rejected / fetch_failed
"""

from __future__ import annotations

import json
import sys

from app.db.session import SessionLocal
from app.services.tw_monthly_revenue.service import collect_latest_monthly_revenue


def main() -> int:
    if SessionLocal is None:
        print("DATABASE_URL is not configured", file=sys.stderr)
        return 2
    with SessionLocal() as session:
        results = collect_latest_monthly_revenue(session)
    payload = [
        {
            "board": item.board,
            "outcome": item.outcome,
            "validationStatus": item.validation_status,
            "reportingPeriod": item.reporting_period_yyyymm,
            "rowCount": item.row_count,
            "rawFetchId": item.raw_fetch_id,
            "stats": item.stats,
            "errors": item.errors,
            "errorMessage": item.error_message,
        }
        for item in results
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if any(item.outcome not in {"accepted", "not_modified"} for item in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
