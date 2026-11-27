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


def test_monthly_revenue_migration_upgrade_preserves_prior_tables(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'monthly_revenue_mig.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    config = _alembic_config(database_url)

    command.upgrade(config, "20261114_0028")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        # Seed a prior research-adjacent row shape that must survive the additive migration.
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS _probe_prior (id INTEGER PRIMARY KEY, note TEXT)"
            )
        )
        # Users table exists after earlier revisions; insert a marker via raw SQL if present.
        tables = set(inspect(engine).get_table_names())
        if "users" in tables:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO users (id, username) VALUES ('u-mig', 'mig-user')"
                )
            )

    command.upgrade(config, "20261127_0029")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "tw_fundamental_raw_fetches" in tables
    assert "tw_monthly_revenue_observations" in tables
    assert "tw_monthly_revenue_revisions" in tables
    raw_columns = {col["name"] for col in inspector.get_columns("tw_fundamental_raw_fetches")}
    assert "body_ref_fetch_id" in raw_columns
    assert "raw_body" in raw_columns
    with engine.begin() as conn:
        if "users" in tables:
            row = conn.execute(text("SELECT username FROM users WHERE id='u-mig'")).fetchone()
            assert row is not None
            assert row[0] == "mig-user"
        conn.execute(
            text(
                "INSERT INTO tw_fundamental_raw_fetches ("
                "source, dataset, board, endpoint_url, request_method, request_headers_json, "
                "fetched_at, parser_version, validation_status, validation_errors_json"
                ") VALUES ("
                "'twse_openapi', 'monthly_revenue', 'TWSE', 'https://example.test', 'GET', '{}', "
                "CURRENT_TIMESTAMP, 'monthly_revenue_v1', 'accepted', '[]')"
            )
        )
        raw_id = conn.execute(text("SELECT id FROM tw_fundamental_raw_fetches LIMIT 1")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO tw_monthly_revenue_observations ("
                "stock_id, board, company_code, period_yyyymm, basis, missing_fields_json, "
                "value_hash, raw_fetch_id, first_seen_at, last_seen_at"
                ") VALUES ("
                "'TW:2330', 'TWSE', '2330', '202610', 'as_published', '{}', "
                f"'hash1', {raw_id}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    from sqlalchemy.exc import IntegrityError

    with engine.begin() as conn:
        raw_id = conn.execute(text("SELECT id FROM tw_fundamental_raw_fetches LIMIT 1")).scalar_one()
        try:
            conn.execute(
                text(
                    "INSERT INTO tw_monthly_revenue_observations ("
                    "stock_id, board, company_code, period_yyyymm, basis, missing_fields_json, "
                    "value_hash, raw_fetch_id, first_seen_at, last_seen_at"
                    ") VALUES ("
                    "'TW:2330', 'TWSE', '2330', '202610', 'as_published', '{}', "
                    f"'hash2', {raw_id}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            raised = False
        except IntegrityError:
            raised = True
        assert raised is True
