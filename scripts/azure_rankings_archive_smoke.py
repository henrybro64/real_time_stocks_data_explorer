#!/usr/bin/env python3
"""Live Azure SQL rankings-archive smoke (#165–#166, #179).

Requires RANKINGS_ARCHIVE_DATABASE_URL. Does not touch DATABASE_URL / Postgres.

Stages:
1. ODBC Driver 18 presence (when pyodbc is installed)
2. Connectivity via SQLAlchemy + disposable ``_poc_smoke`` table
3. Archive schema ensure + table accessibility
4. Repository history-dates query (same path as GET /history/dates)
5. Optional readback of one real archive when present (warn if empty)
6. Disposable write/read/reconstruct of a far-future smoke snapshot (cleaned up)

Never prints connection strings, passwords, or secret values.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path


def _ensure_backend_on_path() -> None:
    candidates = (
        Path(__file__).resolve().parents[1] / "backend",
        Path("/app/backend"),
    )
    for backend_root in candidates:
        if backend_root.is_dir():
            path = str(backend_root)
            if path not in sys.path:
                sys.path.insert(0, path)
            return


class StageReporter:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def record(self, name: str, status: str) -> None:
        self.rows.append((name, status))
        print(f"[smoke] {name}: {status}")

    def write_github_summary(self) -> None:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
        if not summary_path:
            return
        lines = ["## Azure rankings archive smoke", ""]
        lines.append("| Stage | Status |")
        lines.append("| --- | --- |")
        for name, status in self.rows:
            lines.append(f"| {name} | {status} |")
        lines.append("")
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))


def _check_odbc_driver(reporter: StageReporter) -> int:
    try:
        import pyodbc
    except ImportError:
        reporter.record("ODBC Driver 18", "FAIL (pyodbc not installed)")
        return 1
    drivers = [str(item) for item in pyodbc.drivers()]
    if any("ODBC Driver 18" in driver for driver in drivers):
        reporter.record("ODBC Driver 18", "PASS")
        return 0
    reporter.record("ODBC Driver 18", "FAIL (driver not listed)")
    print("available ODBC drivers:", drivers, file=sys.stderr)
    return 1


def _connectivity_smoke(url: str, reporter: StageReporter) -> int:
    from sqlalchemy import create_engine, text

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
            reporter.record("SQL connectivity", "FAIL (insert/select)")
            return 1
        conn.execute(text("DELETE FROM _poc_smoke WHERE note = :note"), {"note": "rankings-archive-poc"})
        conn.execute(text("DROP TABLE _poc_smoke"))
    reporter.record("SQL connectivity", "PASS")
    return 0


def _archive_path_smoke(reporter: StageReporter) -> int:
    _ensure_backend_on_path()
    from sqlalchemy import delete, inspect, select, text

    from app.db.archive_models import RankingSnapshotArchive, RankingSnapshotItemArchive
    from app.db.archive_session import reset_rankings_archive_session_state
    from app.repositories.rankings_archive_repository import RankingsArchiveRepository
    from app.schemas.rankings import (
        RankingItem,
        RankingResponse,
        RankingSourceType,
        RankingType,
    )
    from app.schemas.stock import StockResponse
    from app.services.rankings_archive_service import RankingsArchiveService

    reset_rankings_archive_session_state()
    repo = RankingsArchiveRepository()
    if not repo.is_available():
        reporter.record("Archive schema", "FAIL (repository unavailable)")
        reporter.record("History dates query", "FAIL")
        reporter.record("Historical data present", "FAIL")
        return 1

    if not repo.ensure_schema():
        reporter.record("Archive schema", "FAIL (ensure_schema)")
        return 1

    factory = repo._session_factory()
    assert factory is not None
    with factory() as session:
        bind = session.get_bind()
        inspector = inspect(bind)
        tables = set(inspector.get_table_names())
        required = {"ranking_snapshots_archive", "ranking_snapshot_items_archive"}
        if not required.issubset(tables):
            reporter.record("Archive schema", f"FAIL (missing {sorted(required - tables)})")
            return 1
        session.execute(text("SELECT TOP 1 id FROM ranking_snapshots_archive"))
        session.execute(text("SELECT TOP 1 id FROM ranking_snapshot_items_archive"))
    reporter.record("Archive schema", "PASS")

    service = RankingsArchiveService(repository=repo)
    try:
        dates = service.list_history_dates(
            market="TW",
            universe="TWSE",
            ranking_type=RankingType.net_buy,
            limit=5,
        )
    except Exception as exc:  # noqa: BLE001
        reporter.record("History dates query", f"FAIL ({type(exc).__name__})")
        return 1
    reporter.record("History dates query", "PASS")

    if dates:
        reporter.record("Historical data present", f"PASS ({len(dates)} date(s))")
        loaded = service.get_history_rankings(
            market="TW",
            universe="TWSE",
            ranking_type=RankingType.net_buy,
            trading_date=dates[0],
            limit=50,
        )
        if loaded is None or not loaded.items:
            reporter.record("Historical readback", "FAIL (empty snapshot)")
            return 1
        reporter.record(
            "Historical readback",
            f"PASS ({len(loaded.items)} item(s) on {dates[0].isoformat()})",
        )
    else:
        reporter.record(
            "Historical data present",
            "WARN (reachable + schema ok, zero archived dates yet)",
        )
        reporter.record("Historical readback", "SKIP")

    trading_date = date(2099, 1, 1)  # far-future disposable smoke key
    market = "US"
    universe = "DJIA"
    ranking_type = RankingType.volume
    response = RankingResponse(
        market=market,
        rankingType=ranking_type,
        twBoard=None,
        universe=universe,
        items=[
            RankingItem(
                rank=1,
                stock=StockResponse(
                    id="US:AAPL",
                    market="US",
                    symbol="AAPL",
                    name="Apple Inc.",
                    exchange="NASDAQ",
                    currency="USD",
                    timezone="America/New_York",
                ),
                lastPrice=190.0,
                changePercent=1.0,
                volume=1_000_000,
                volumeDeltaPercent=None,
                netBuySellShares=None,
                peRatio=28.0,
                peRatioType="trailing",
                peRatioSource="smoke",
                valuationAsOf="2099-01-01",
                valuationWarning=None,
                timestamp="2099-01-01T16:00:00-05:00",
            ),
            RankingItem(
                rank=2,
                stock=StockResponse(
                    id="US:MSFT",
                    market="US",
                    symbol="MSFT",
                    name="Microsoft",
                    exchange="NASDAQ",
                    currency="USD",
                    timezone="America/New_York",
                ),
                lastPrice=400.0,
                changePercent=0.5,
                volume=900_000,
                volumeDeltaPercent=None,
                netBuySellShares=None,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp="2099-01-01T16:00:00-05:00",
            ),
        ],
        timestamp="2099-01-01T21:00:00+00:00",
        source="azure-archive-smoke",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=False,
        dataFreshnessLabel="smoke",
        warningMessage=None,
        unsupportedReason=None,
        usRankingsScope="djia",
        twRankingsScope=None,
    )

    try:
        written = repo.upsert_eod_snapshot(
            market=market,
            universe=universe,
            ranking_type=ranking_type,
            trading_date=trading_date,
            response=response,
            captured_at=datetime(2099, 1, 1, 21, 0, tzinfo=timezone.utc),
        )
        if written is None:
            reporter.record("Disposable archive write/read", "FAIL (upsert None)")
            return 1
        loaded = repo.load_eod_snapshot(
            market=market,
            universe=universe,
            ranking_type=ranking_type,
            trading_date=trading_date,
        )
        if loaded is None:
            reporter.record("Disposable archive write/read", "FAIL (load None)")
            return 1
        reconstructed = repo.to_ranking_response(loaded)
        symbols = [item.stock.symbol for item in reconstructed.items]
        if symbols != ["AAPL", "MSFT"]:
            reporter.record(
                "Disposable archive write/read",
                f"FAIL (order {symbols})",
            )
            return 1
        reporter.record("Disposable archive write/read", "PASS")
        return 0
    finally:
        try:
            with factory() as session:
                snap = session.execute(
                    select(RankingSnapshotArchive).where(
                        RankingSnapshotArchive.market == market,
                        RankingSnapshotArchive.universe == universe,
                        RankingSnapshotArchive.ranking_type == ranking_type.value,
                        RankingSnapshotArchive.trading_date == trading_date,
                    )
                ).scalar_one_or_none()
                if snap is not None:
                    session.execute(
                        delete(RankingSnapshotItemArchive).where(
                            RankingSnapshotItemArchive.snapshot_id == snap.id
                        )
                    )
                    session.execute(
                        delete(RankingSnapshotArchive).where(RankingSnapshotArchive.id == snap.id)
                    )
                    session.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"archive smoke cleanup warning: {type(exc).__name__}", file=sys.stderr)
        reset_rankings_archive_session_state()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ci-summary",
        action="store_true",
        help="Append stage results to $GITHUB_STEP_SUMMARY when set",
    )
    args = parser.parse_args(argv)
    reporter = StageReporter()

    url = os.environ.get("RANKINGS_ARCHIVE_DATABASE_URL", "").strip()
    if not url:
        print("RANKINGS_ARCHIVE_DATABASE_URL is not set", file=sys.stderr)
        reporter.record("SQL connectivity", "FAIL (env unset)")
        if args.ci_summary:
            reporter.write_github_summary()
        return 2

    try:
        from sqlalchemy import create_engine  # noqa: F401
    except ImportError:
        print("sqlalchemy is required", file=sys.stderr)
        reporter.record("SQL connectivity", "FAIL (sqlalchemy missing)")
        if args.ci_summary:
            reporter.write_github_summary()
        return 2

    odbc_rc = _check_odbc_driver(reporter)
    if odbc_rc != 0:
        if args.ci_summary:
            reporter.write_github_summary()
        return odbc_rc

    try:
        connectivity_rc = _connectivity_smoke(url, reporter)
    except Exception as exc:  # noqa: BLE001
        reporter.record("SQL connectivity", f"FAIL ({type(exc).__name__})")
        print(f"connectivity smoke failed: {type(exc).__name__}", file=sys.stderr)
        if args.ci_summary:
            reporter.write_github_summary()
        return 1
    if connectivity_rc != 0:
        if args.ci_summary:
            reporter.write_github_summary()
        return connectivity_rc

    try:
        archive_rc = _archive_path_smoke(reporter)
    except Exception as exc:  # noqa: BLE001
        reporter.record("Archive path", f"FAIL ({type(exc).__name__})")
        print(f"archive path smoke failed: {type(exc).__name__}", file=sys.stderr)
        if args.ci_summary:
            reporter.write_github_summary()
        return 1

    if args.ci_summary:
        reporter.write_github_summary()
    return archive_rc


if __name__ == "__main__":
    raise SystemExit(main())
