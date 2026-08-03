from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import TaiwanInstitutionalDaily, TaiwanMarginShortDaily

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(_BACKEND_ROOT))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_taiwan_market_daily_migration_creates_tables_with_constraints(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'taiwan_market_daily.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "taiwan_institutional_daily" in tables
    assert "taiwan_margin_short_daily" in tables

    institutional_unique = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("taiwan_institutional_daily")
    }
    margin_unique = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("taiwan_margin_short_daily")
    }
    assert "uq_taiwan_institutional_daily_market_symbol_date" in institutional_unique
    assert "uq_taiwan_margin_short_daily_market_symbol_date" in margin_unique

    institutional_indexes = {
        index["name"] for index in inspector.get_indexes("taiwan_institutional_daily")
    }
    margin_indexes = {
        index["name"] for index in inspector.get_indexes("taiwan_margin_short_daily")
    }
    assert "ix_taiwan_institutional_daily_lookup" in institutional_indexes
    assert "ix_taiwan_margin_short_daily_lookup" in margin_indexes

    margin_columns = {
        column["name"]: column
        for column in inspector.get_columns("taiwan_margin_short_daily")
    }
    assert margin_columns["margin_balance_lots"]["nullable"] is True
    assert margin_columns["short_margin_balance_lots"]["nullable"] is True
    assert margin_columns["borrowed_short_balance_lots"]["nullable"] is True


def test_taiwan_institutional_daily_prevents_duplicate_market_symbol_date(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'taiwan_institutional_dup.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    trade_date = date(2026, 7, 9)
    with session_local() as session:
        session.add(
            TaiwanInstitutionalDaily(
                market="TWSE",
                symbol="2330",
                date=trade_date,
                name="台積電",
                foreign_net_shares=100,
                investment_trust_net_shares=50,
                dealer_self_net_shares=10,
                dealer_hedge_net_shares=20,
                dealer_net_shares=30,
                total_net_shares=180,
                source="twse",
            )
        )
        session.commit()
        session.add(
            TaiwanInstitutionalDaily(
                market="TWSE",
                symbol="2330",
                date=trade_date,
                name="台積電",
                foreign_net_shares=200,
                investment_trust_net_shares=50,
                dealer_self_net_shares=10,
                dealer_hedge_net_shares=20,
                dealer_net_shares=30,
                total_net_shares=280,
                source="twse-dup",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_margin_short_nullable_migration_downgrade_drops_partial_rows(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'taiwan_margin_short_downgrade.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    with session_local() as session:
        session.add(
            TaiwanMarginShortDaily(
                market="TWSE",
                symbol="1303",
                date=date(2026, 7, 9),
                name="南亞",
                close_price=185.0,
                margin_balance_lots=31_339.0,
                short_margin_balance_lots=0.0,
                borrowed_short_balance_lots=None,
                borrowed_short_balance_change_lots=None,
                borrowed_short_balance_change_amount_10k=None,
                source="twse-partial",
            )
        )
        session.add(
            TaiwanMarginShortDaily(
                market="TWSE",
                symbol="2330",
                date=date(2026, 7, 9),
                name="台積電",
                close_price=1000.0,
                margin_balance_lots=10.0,
                short_margin_balance_lots=1.0,
                borrowed_short_balance_lots=2.0,
                borrowed_short_balance_change_lots=0.1,
                borrowed_short_balance_change_amount_10k=1,
                source="twse-complete",
            )
        )
        session.commit()

    command.downgrade(config, "20260712_0011")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    margin_columns = {
        column["name"]: column
        for column in inspector.get_columns("taiwan_margin_short_daily")
    }
    assert margin_columns["margin_balance_lots"]["nullable"] is False
    assert margin_columns["borrowed_short_balance_lots"]["nullable"] is False

    session_local = sessionmaker(bind=engine)
    with session_local() as session:
        rows = session.query(TaiwanMarginShortDaily).all()
        assert len(rows) == 1
        assert rows[0].symbol == "2330"
        assert rows[0].borrowed_short_balance_lots == pytest.approx(2.0)


def test_taiwan_margin_short_daily_prevents_duplicate_market_symbol_date(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'taiwan_margin_short_dup.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    session_local = sessionmaker(bind=engine)
    trade_date = date(2026, 7, 9)
    with session_local() as session:
        session.add(
            TaiwanMarginShortDaily(
                market="TPEx",
                symbol="6488",
                date=trade_date,
                name="環球晶",
                close_price=100.0,
                margin_balance_lots=10.0,
                short_margin_balance_lots=2.0,
                borrowed_short_balance_lots=3.0,
                borrowed_short_balance_change_lots=0.5,
                borrowed_short_balance_change_amount_10k=1,
                source="tpex",
            )
        )
        session.commit()
        session.add(
            TaiwanMarginShortDaily(
                market="TPEx",
                symbol="6488",
                date=trade_date,
                name="環球晶",
                close_price=101.0,
                margin_balance_lots=11.0,
                short_margin_balance_lots=2.0,
                borrowed_short_balance_lots=3.0,
                borrowed_short_balance_change_lots=0.5,
                borrowed_short_balance_change_amount_10k=1,
                source="tpex-dup",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
