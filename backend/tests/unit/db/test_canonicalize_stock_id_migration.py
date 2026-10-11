from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(_BACKEND_ROOT))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_migration_collapses_mixed_case_watchlist_and_positions(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'canonical_ids.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    config = _alembic_config(database_url)
    command.upgrade(config, "20261011_0019")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, username) VALUES ('user-1', 'user-1')")
        )
        connection.execute(
            text(
                "INSERT INTO watchlist_items (user_id, stock_id) "
                "VALUES ('user-1', 'US:aapl'), ('user-1', 'US:AAPL'), "
                "('user-1', 'tw:2330')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO portfolio_positions "
                "(user_id, stock_id, quantity, average_cost, currency) "
                "VALUES ('user-1', 'US:aapl', 2, 10, 'USD'), "
                "('user-1', 'US:AAPL', 1, 40, 'USD')"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        watchlist = {
            row.stock_id
            for row in connection.execute(
                text("SELECT stock_id FROM watchlist_items WHERE user_id = 'user-1'")
            )
        }
        positions = connection.execute(
            text(
                "SELECT stock_id, quantity, average_cost FROM portfolio_positions "
                "WHERE user_id = 'user-1'"
            )
        ).all()

    assert watchlist == {"US:AAPL", "TW:2330"}
    assert len(positions) == 1
    assert positions[0].stock_id == "US:AAPL"
    assert positions[0].quantity == 3
    # 2 shares at 10 and 1 share at 40.
    assert positions[0].average_cost == 20
