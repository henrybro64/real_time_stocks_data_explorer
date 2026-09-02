from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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

    command.downgrade(_alembic_config(database_url), "20260816_0013")
    inspector = inspect(engine)
    assert "realized_trades" not in inspector.get_table_names()
