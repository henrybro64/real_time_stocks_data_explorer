"""Disposable-database guard for destructive integration fixtures."""

from __future__ import annotations

import pytest

from tests.support import assert_disposable_integration_database


@pytest.mark.parametrize(
    ("url", "environ"),
    [
        (
            "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks_test",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "1"},
        ),
        (
            "postgresql+psycopg://henrybro@localhost:5432/research_test",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "true"},
        ),
        (
            "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks_test",
            {"CI": "true"},
        ),
    ],
)
def test_disposable_database_urls_allowed(url: str, environ: dict[str, str]) -> None:
    assert_disposable_integration_database(url, environ=environ)


@pytest.mark.parametrize(
    ("url", "environ", "match"),
    [
        (
            "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "1"},
            "blocked database",
        ),
        (
            "postgresql+psycopg://henrybro@127.0.0.1:5432/sto",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "1"},
            "blocked database",
        ),
        (
            "postgresql+psycopg://postgres@127.0.0.1:5432/postgres",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "1"},
            "blocked database",
        ),
        (
            "postgresql+psycopg://postgres@db.example.com:5432/stocks_test",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "1"},
            "non-local host",
        ),
        (
            "postgresql+psycopg://postgres@127.0.0.1:5432/stocks_test",
            {},
            "ALLOW_DESTRUCTIVE_DB_TESTS",
        ),
        (
            "postgresql+psycopg://postgres@127.0.0.1:5432/research_test",
            {"CI": "true"},
            "ALLOW_DESTRUCTIVE_DB_TESTS",
        ),
        (
            "sqlite+pysqlite:///tmp/stocks_test.db",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "1"},
            "unsupported driver",
        ),
        (
            "postgresql+psycopg://postgres@127.0.0.1:5432/",
            {"ALLOW_DESTRUCTIVE_DB_TESTS": "1"},
            "no database name",
        ),
    ],
)
def test_non_disposable_database_urls_rejected(
    url: str, environ: dict[str, str], match: str
) -> None:
    with pytest.raises(RuntimeError, match=match):
        assert_disposable_integration_database(url, environ=environ)
