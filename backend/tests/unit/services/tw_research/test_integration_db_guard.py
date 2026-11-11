"""Disposable-database guard for destructive integration fixtures."""

from __future__ import annotations

import pytest

from tests.support import assert_disposable_integration_database


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks_test",
        "postgresql+psycopg://henrybro@127.0.0.1:5432/research_test",
        "postgresql+psycopg://u:p@localhost/foo_test",
    ],
)
def test_disposable_database_urls_allowed(url: str) -> None:
    assert_disposable_integration_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks",
        "postgresql+psycopg://henrybro@127.0.0.1:5432/sto",
        "postgresql+psycopg://postgres@127.0.0.1:5432/postgres",
        "postgresql+psycopg://postgres@127.0.0.1:5432/",
    ],
)
def test_non_disposable_database_urls_rejected(url: str) -> None:
    with pytest.raises(RuntimeError, match="Refusing destructive"):
        assert_disposable_integration_database(url)
