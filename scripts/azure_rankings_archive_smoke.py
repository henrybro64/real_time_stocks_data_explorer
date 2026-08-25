#!/usr/bin/env python3
"""Manual connectivity + archive write/read smoke for Azure SQL rankings archive (#165–#166).

Requires RANKINGS_ARCHIVE_DATABASE_URL. Does not touch DATABASE_URL / Postgres.

Steps:
1. Connect and exercise a disposable ``_poc_smoke`` table (connectivity).
2. Ensure archive schema, upsert one non-personal EOD snapshot, load it back,
   reconstruct RankingResponse rank order, then delete the smoke archive rows.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path


def _ensure_backend_on_path() -> None:
    backend_root = Path(__file__).resolve().parents[1] / "backend"
    path = str(backend_root)
    if path not in sys.path:
        sys.path.insert(0, path)


def _connectivity_smoke(url: str) -> tuple[str, object]:
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
            raise RuntimeError("smoke insert/select failed")
        conn.execute(text("DELETE FROM _poc_smoke WHERE note = :note"), {"note": "rankings-archive-poc"})
        conn.execute(text("DROP TABLE _poc_smoke"))
    return row[0], row[1]


def _archive_write_read_smoke() -> int:
    _ensure_backend_on_path()
    from sqlalchemy import delete, select

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

    reset_rankings_archive_session_state()
    repo = RankingsArchiveRepository()
    if not repo.is_available():
        print("archive repository unavailable after connect", file=sys.stderr)
        return 1

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
            print("archive upsert returned None", file=sys.stderr)
            return 1
        loaded = repo.load_eod_snapshot(
            market=market,
            universe=universe,
            ranking_type=ranking_type,
            trading_date=trading_date,
        )
        if loaded is None:
            print("archive load returned None", file=sys.stderr)
            return 1
        reconstructed = repo.to_ranking_response(loaded)
        symbols = [item.stock.symbol for item in reconstructed.items]
        if symbols != ["AAPL", "MSFT"]:
            print(f"archive reconstruct order wrong: {symbols}", file=sys.stderr)
            return 1
        print(
            "azure rankings archive write/read ok:",
            loaded.snapshot.id,
            symbols,
            reconstructed.universe,
        )
        return 0
    finally:
        # Best-effort cleanup of the disposable smoke snapshot.
        try:
            factory = repo._session_factory()
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
            print(f"archive smoke cleanup warning: {exc}", file=sys.stderr)
        reset_rankings_archive_session_state()


def main() -> int:
    url = os.environ.get("RANKINGS_ARCHIVE_DATABASE_URL", "").strip()
    if not url:
        print("RANKINGS_ARCHIVE_DATABASE_URL is not set", file=sys.stderr)
        return 2

    try:
        from sqlalchemy import create_engine  # noqa: F401
    except ImportError:
        print("sqlalchemy is required", file=sys.stderr)
        return 2

    try:
        note, created_at = _connectivity_smoke(url)
    except Exception as exc:  # noqa: BLE001
        print(f"connectivity smoke failed: {exc}", file=sys.stderr)
        return 1
    print("azure rankings archive connectivity smoke ok:", note, created_at)

    return _archive_write_read_smoke()


if __name__ == "__main__":
    raise SystemExit(main())
