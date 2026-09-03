from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(_BACKEND_ROOT))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_realized_trades_migration_creates_table_with_constraints(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'realized_trades.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "realized_trades" in inspector.get_table_names()

    indexes = {index["name"] for index in inspector.get_indexes("realized_trades")}
    assert "ix_realized_trades_user_sell_date" in indexes

    columns = {column["name"]: column for column in inspector.get_columns("realized_trades")}
    assert "cumulative_dividends" in columns
    assert columns["cumulative_dividends"]["nullable"] is False

    check_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("realized_trades")
    }
    assert "ck_realized_trades_cumulative_dividends_non_negative" in check_names

    command.downgrade(_alembic_config(database_url), "20260902_0014")
    inspector = inspect(engine)
    columns_after = {
        column["name"] for column in inspector.get_columns("realized_trades")
    }
    assert "cumulative_dividends" not in columns_after

    command.downgrade(_alembic_config(database_url), "20260816_0013")
    inspector = inspect(engine)
    assert "realized_trades" not in inspector.get_table_names()


def test_cumulative_dividends_migration_backfills_existing_rows(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'realized_trades_backfill.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    config = _alembic_config(database_url)

    command.upgrade(config, "20260902_0014")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash) "
                "VALUES ('user-1', 'user-1', NULL)",
            ),
        )
        connection.execute(
            text(
                """
                INSERT INTO realized_trades (
                    user_id, symbol, currency, buy_price, sell_price, shares,
                    buy_date, sell_date, created_at, updated_at
                ) VALUES (
                    'user-1', '2330', 'TWD', 920, 1020, 1000, NULL, '2026-08-01',
                    '2026-08-01 00:00:00', '2026-08-01 00:00:00'
                )
                """,
            ),
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        dividends = connection.execute(
            text("SELECT cumulative_dividends FROM realized_trades WHERE symbol = '2330'"),
        ).scalar_one()
    assert dividends == 0
