#!/usr/bin/env python3
"""Manual connectivity smoke test for the Azure SQL rankings archive PoC (#165).

Requires RANKINGS_ARCHIVE_DATABASE_URL. Does not touch DATABASE_URL / Postgres.
Creates a disposable _poc_smoke table, inserts/reads/deletes one row, then drops it.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone


def main() -> int:
    url = os.environ.get("RANKINGS_ARCHIVE_DATABASE_URL", "").strip()
    if not url:
        print("RANKINGS_ARCHIVE_DATABASE_URL is not set", file=sys.stderr)
        return 2

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("sqlalchemy is required", file=sys.stderr)
        return 2

    engine = create_engine(url, pool_pre_ping=True)
    marker = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                IF OBJECT_ID('_poc_smoke', 'U') IS NOT NULL DROP TABLE _poc_smoke;
                CREATE TABLE _poc_smoke (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    note NVARCHAR(128) NOT NULL,
                    created_at DATETIMEOFFSET NOT NULL
                );
                """
            )
        )
        conn.execute(
            text("INSERT INTO _poc_smoke (note, created_at) VALUES (:note, :created_at)"),
            {"note": "rankings-archive-poc", "created_at": marker},
        )
        row = conn.execute(
            text("SELECT note, created_at FROM _poc_smoke WHERE note = :note"),
            {"note": "rankings-archive-poc"},
        ).first()
        if row is None:
            print("smoke insert/select failed", file=sys.stderr)
            return 1
        conn.execute(text("DELETE FROM _poc_smoke WHERE note = :note"), {"note": "rankings-archive-poc"})
        conn.execute(text("DROP TABLE _poc_smoke"))

    print("azure rankings archive smoke ok:", row[0], row[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
