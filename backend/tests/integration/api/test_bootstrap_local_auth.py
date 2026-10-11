"""Bootstrap local auth is create-only by default (#286)."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.db.models import User
from app.db.session import SessionLocal
from app.services.local_auth_service import (
    ensure_bootstrap_local_user,
    verify_local_login,
)

pytestmark = pytest.mark.integration


def test_bootstrap_create_only_and_explicit_reset() -> None:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")

    with SessionLocal() as session:
        session.execute(delete(User).where(User.username == "bootstrap-admin"))
        session.commit()

    assert ensure_bootstrap_local_user("bootstrap-admin", "first-password-1") == "created"
    assert verify_local_login("bootstrap-admin", "first-password-1") is not None

    assert (
        ensure_bootstrap_local_user("bootstrap-admin", "second-password-2") == "unchanged"
    )
    assert verify_local_login("bootstrap-admin", "first-password-1") is not None
    assert verify_local_login("bootstrap-admin", "second-password-2") is None

    assert (
        ensure_bootstrap_local_user(
            "bootstrap-admin",
            "second-password-2",
            reset_password=True,
        )
        == "reset"
    )
    assert verify_local_login("bootstrap-admin", "second-password-2") is not None

    with SessionLocal() as session:
        row = session.execute(
            select(User).where(User.username == "bootstrap-admin"),
        ).scalar_one()
        assert row.password_hash is not None
