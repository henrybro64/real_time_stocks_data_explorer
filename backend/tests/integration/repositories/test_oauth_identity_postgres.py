"""PostgreSQL regression for Entra first-time identity insert (#301)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from sqlalchemy import func, select

from app.db.models import OAuthIdentity, User, WatchlistItem
from app.db.session import SessionLocal
from app.repositories.oauth_identity_repository import OAuthIdentityRepository

pytestmark = pytest.mark.integration

_PROVIDER = "entra_external_id"


def _count(model) -> int:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    with SessionLocal() as session:
        return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def test_postgres_first_time_entra_insert_does_not_violate_fk() -> None:
    repo = OAuthIdentityRepository()
    user_id = repo.create_user_with_identity(
        user_id="u-pg-entra-1",
        username="pg-entra-user",
        provider=_PROVIDER,
        subject="oid-pg-1",
    )
    assert user_id == "u-pg-entra-1"
    assert repo.get_user_id(provider=_PROVIDER, subject="oid-pg-1") == "u-pg-entra-1"
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    with SessionLocal() as session:
        user = session.execute(select(User).where(User.id == "u-pg-entra-1")).scalar_one()
        identity = session.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == _PROVIDER,
                OAuthIdentity.subject == "oid-pg-1",
            )
        ).scalar_one()
        assert user.username == "pg-entra-user"
        assert identity.user_id == user.id


def test_postgres_repeat_login_reuses_user() -> None:
    repo = OAuthIdentityRepository()
    first = repo.create_user_with_identity(
        user_id="u-pg-repeat-a",
        username="pg-repeat",
        provider=_PROVIDER,
        subject="oid-pg-repeat",
    )
    second = repo.create_user_with_identity(
        user_id="u-pg-repeat-b",
        username="pg-repeat",
        provider=_PROVIDER,
        subject="oid-pg-repeat",
    )
    assert first == second == "u-pg-repeat-a"
    assert _count(OAuthIdentity) == 1
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    with SessionLocal() as session:
        users = session.execute(select(User).where(User.username.like("pg-repeat%"))).scalars().all()
    assert [row.id for row in users] == ["u-pg-repeat-a"]


def test_postgres_username_collision_does_not_merge_local_account() -> None:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    with SessionLocal() as session:
        session.add(User(id="u-pg-local", username="shared-name", password_hash="hash"))
        session.add(WatchlistItem(user_id="u-pg-local", stock_id="US:MSFT"))
        session.commit()

    repo = OAuthIdentityRepository()
    entra_id = repo.create_user_with_identity(
        user_id="u-pg-entra",
        username="shared-name",
        provider=_PROVIDER,
        subject="oid-pg-shared",
    )
    assert entra_id == "u-pg-entra"
    with SessionLocal() as session:
        local = session.execute(select(User).where(User.id == "u-pg-local")).scalar_one()
        entra = session.execute(select(User).where(User.id == "u-pg-entra")).scalar_one()
        watchlist = session.execute(
            select(WatchlistItem).where(WatchlistItem.user_id == "u-pg-local"),
        ).scalars().all()
        assert local.username == "shared-name"
        assert local.password_hash == "hash"
        assert entra.username == "shared-name-1"
        assert entra.password_hash is None
        assert len(watchlist) == 1
        assert watchlist[0].stock_id == "US:MSFT"


def test_postgres_keeps_a_long_suffixed_username() -> None:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    base = "b" * 48
    chosen = f"{base}-9"
    with SessionLocal() as session:
        session.add(User(id="u-pg-long-local", username=base, password_hash="hash"))
        session.commit()

    repo = OAuthIdentityRepository()
    entra_id = repo.create_user_with_identity(
        user_id="u-pg-long-entra",
        username=chosen,
        provider=_PROVIDER,
        subject="oid-pg-long",
    )
    assert entra_id == "u-pg-long-entra"
    with SessionLocal() as session:
        local = session.execute(select(User).where(User.id == "u-pg-long-local")).scalar_one()
        entra = session.execute(select(User).where(User.id == "u-pg-long-entra")).scalar_one()
        assert local.username == base
        assert local.password_hash == "hash"
        assert entra.username == chosen


def test_postgres_username_retry_reaches_the_ninth_suffix() -> None:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    stem = "c" * 48
    with SessionLocal() as session:
        session.add(User(id="u-pg-stem-0", username=stem, password_hash=None))
        for index in range(1, 9):
            session.add(
                User(id=f"u-pg-stem-{index}", username=f"{stem}-{index}", password_hash=None)
            )
        session.commit()

    repo = OAuthIdentityRepository()
    repo.create_user_with_identity(
        user_id="u-pg-stem-next",
        username=stem,
        provider=_PROVIDER,
        subject="oid-pg-stem-next",
    )
    with SessionLocal() as session:
        created = session.execute(select(User).where(User.id == "u-pg-stem-next")).scalar_one()
    assert created.username == f"{stem}-9"


def test_postgres_concurrent_first_time_callbacks_share_one_user() -> None:
    repo = OAuthIdentityRepository()
    subject = "oid-pg-race"
    username = "pg-race-user"

    def _attempt(index: int) -> str:
        return repo.create_user_with_identity(
            user_id=f"u-pg-race-{index}",
            username=username,
            provider=_PROVIDER,
            subject=subject,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_attempt, index) for index in range(8)]
        user_ids = [future.result() for future in as_completed(futures)]

    assert len(set(user_ids)) == 1
    assert repo.get_user_id(provider=_PROVIDER, subject=subject) == user_ids[0]
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")
    with SessionLocal() as session:
        identities = session.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == _PROVIDER,
                OAuthIdentity.subject == subject,
            )
        ).scalars().all()
        users = session.execute(select(User).where(User.username.like("pg-race-user%"))).scalars().all()
    assert len(identities) == 1
    assert len(users) == 1
    assert users[0].id == identities[0].user_id
