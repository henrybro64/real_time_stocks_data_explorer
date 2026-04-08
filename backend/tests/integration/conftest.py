from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if not DATABASE_URL:
    pytest.skip(
        "integration tests require DATABASE_URL",
        allow_module_level=True,
    )

from app.db.init_ids import DEFAULT_USER_ID
from app.db.models import Base, User
from app.db.session import SessionLocal, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def setup_database() -> Iterator[None]:
    if engine is None:
        raise RuntimeError("DATABASE_URL is configured but SQLAlchemy engine is missing")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_database() -> Iterator[None]:
    if engine is None or SessionLocal is None:
        raise RuntimeError("DATABASE_URL is configured but SessionLocal is missing")

    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(
                text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE')
            )

    with SessionLocal() as session:
        session.add(
            User(
                id=DEFAULT_USER_ID,
                username=DEFAULT_USER_ID,
                password_hash=None,
            )
        )
        session.commit()

    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session() -> Iterator[Session]:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    with SessionLocal() as session:
        yield session
