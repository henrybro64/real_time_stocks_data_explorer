from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import RankingSnapshot, RankingSnapshotItem

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(_BACKEND_ROOT))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_ranking_snapshots_migration_creates_tables_with_constraints(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ranking_snapshots.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "ranking_snapshots" in tables
    assert "ranking_snapshot_items" in tables

    snapshot_fks = {
        fk["constrained_columns"][0]: fk["referred_table"]
        for fk in inspector.get_foreign_keys("ranking_snapshot_items")
    }
    assert snapshot_fks["snapshot_id"] == "ranking_snapshots"

    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("ranking_snapshots")
    }
    assert "uq_ranking_snapshots_market_board_type_bucket_scope" in unique_constraints


def test_ranking_snapshots_migration_prevents_duplicate_bucket_rows(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ranking_snapshots_dup.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    bucket_start = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    snapshot = RankingSnapshot(
        market="US",
        tw_board="",
        ranking_type="volume",
        bucket_start=bucket_start,
        source="Quote rankings from the app's free best-effort quote providers.",
        source_type="free-unofficial",
        source_freshness="Market open; rankings use the latest quote snapshot.",
        is_market_open=True,
        warning_message=None,
        unsupported_reason=None,
    )

    with session_local() as session:
        session.add(snapshot)
        session.commit()
        session.add(
            RankingSnapshot(
                market="US",
                tw_board="",
                ranking_type="volume",
                bucket_start=bucket_start,
                source="duplicate",
                source_type="free-unofficial",
                source_freshness="duplicate",
                is_market_open=True,
                warning_message=None,
                unsupported_reason=None,
                capture_scope="on_demand",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_ranking_snapshots_migration_allows_same_bucket_different_capture_scope(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ranking_snapshots_scope.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    bucket_start = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    with session_local() as session:
        session.add(
            RankingSnapshot(
                market="US",
                tw_board="",
                ranking_type="volume",
                bucket_start=bucket_start,
                source="on-demand",
                source_type="free-unofficial",
                source_freshness="on-demand",
                is_market_open=True,
                warning_message=None,
                unsupported_reason=None,
                capture_scope="on_demand",
            )
        )
        session.add(
            RankingSnapshot(
                market="US",
                tw_board="",
                ranking_type="volume",
                bucket_start=bucket_start,
                source="scheduler",
                source_type="free-unofficial",
                source_freshness="scheduler",
                is_market_open=True,
                warning_message=None,
                unsupported_reason=None,
                capture_scope="scheduler_bounded",
            )
        )
        session.commit()


def test_ranking_snapshot_items_store_raw_payload_json(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ranking_snapshots_payload.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    with session_local() as session:
        snapshot = RankingSnapshot(
            market="TW",
            tw_board="TWSE",
            ranking_type="volume",
            bucket_start=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),
            source="Quote rankings from the app's free best-effort quote providers.",
            source_type="free-unofficial",
            source_freshness="test",
            is_market_open=True,
            warning_message=None,
            unsupported_reason=None,
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RankingSnapshotItem(
                snapshot_id=snapshot.id,
                rank=1,
                stock_id="TW:2330",
                symbol="2330",
                name="台積電",
                last_price=950.0,
                change_percent=1.2,
                volume=123_456,
                volume_delta=None,
                volume_delta_percent=None,
                net_buy=None,
                net_sell=None,
                data_time="2026-07-03T09:30:00+08:00",
                raw_payload_json={"stock": {"id": "TW:2330"}, "peRatio": 18.5},
            )
        )
        session.commit()

    with session_local() as session:
        item = session.execute(select(RankingSnapshotItem)).scalar_one()
        assert item.raw_payload_json == {"stock": {"id": "TW:2330"}, "peRatio": 18.5}


def test_ranking_snapshots_accept_nasdaq100_universe_key(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ranking_snapshots_nasdaq.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    snapshot = RankingSnapshot(
        market="US",
        tw_board="NASDAQ100",
        ranking_type="volume",
        bucket_start=datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc),
        source="Nasdaq-100 constituent quote rankings from the app's free best-effort quote providers.",
        source_type="free-unofficial",
        source_freshness="Market open; rankings use the latest quote snapshot.",
        is_market_open=True,
        warning_message=None,
        unsupported_reason=None,
    )
    with session_local() as session:
        session.add(snapshot)
        session.commit()
        loaded = session.execute(select(RankingSnapshot)).scalar_one()
        assert loaded.tw_board == "NASDAQ100"


def test_ranking_snapshots_downgrade_removes_long_universe_keys(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ranking_snapshots_downgrade.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    bucket_start = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    with session_local() as session:
        session.add(
            RankingSnapshot(
                market="US",
                tw_board="DJIA",
                ranking_type="volume",
                bucket_start=bucket_start,
                source="djia",
                source_type="free-unofficial",
                source_freshness="test",
                is_market_open=True,
                warning_message=None,
                unsupported_reason=None,
            )
        )
        session.add(
            RankingSnapshot(
                market="US",
                tw_board="NASDAQ100",
                ranking_type="volume",
                bucket_start=bucket_start,
                source="nasdaq",
                source_type="free-unofficial",
                source_freshness="test",
                is_market_open=True,
                warning_message=None,
                unsupported_reason=None,
            )
        )
        session.commit()

    command.downgrade(config, "20260803_0012")
    with session_local() as session:
        boards = {row.tw_board for row in session.execute(select(RankingSnapshot)).scalars()}
    assert boards == {"DJIA"}

