"""OAuth identity persistence inserts the user before the FK child (#301)."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import OAuthIdentity, User, WatchlistItem
from app.repositories.oauth_identity_repository import (
    OAuthIdentityPersistError,
    OAuthIdentityRepository,
)
from tests.support import install_in_memory_sessionlocal, seed_test_users

_PROVIDER = "entra_external_id"


@pytest.fixture
def oauth_repo(monkeypatch: pytest.MonkeyPatch) -> tuple[OAuthIdentityRepository, sessionmaker]:
    session_local = install_in_memory_sessionlocal(monkeypatch)
    return OAuthIdentityRepository(), session_local


def _count(session_local: sessionmaker, model) -> int:
    with session_local() as session:
        return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def test_child_insert_without_parent_is_rejected_and_rolled_back(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
) -> None:
    _repo, session_local = oauth_repo
    with session_local() as session:
        session.add(
            OAuthIdentity(
                user_id="u-missing",
                provider=_PROVIDER,
                subject="sub-orphan",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
    assert _count(session_local, User) == 0
    assert _count(session_local, OAuthIdentity) == 0


def test_create_user_with_identity_inserts_user_before_oauth_row(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
) -> None:
    repo, session_local = oauth_repo
    inserts: list[str] = []

    @event.listens_for(session_local.kw["bind"], "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        stripped = " ".join(statement.split()).upper()
        if stripped.startswith("INSERT INTO"):
            table = stripped.split()[2].strip('"')
            inserts.append(table.lower())

    user_id = repo.create_user_with_identity(
        user_id="u-entra-1",
        username="alice.entra",
        provider=_PROVIDER,
        subject="oid-subject-1",
    )
    assert user_id == "u-entra-1"
    assert inserts.index("users") < inserts.index("oauth_identities")
    assert repo.get_user_id(provider=_PROVIDER, subject="oid-subject-1") == "u-entra-1"
    with session_local() as session:
        user = session.execute(select(User).where(User.id == "u-entra-1")).scalar_one()
        identity = session.execute(
            select(OAuthIdentity).where(OAuthIdentity.subject == "oid-subject-1"),
        ).scalar_one()
        assert user.username == "alice.entra"
        assert user.password_hash is None
        assert identity.user_id == "u-entra-1"


def test_repeat_create_reuses_the_same_user_id(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
) -> None:
    repo, session_local = oauth_repo
    first = repo.create_user_with_identity(
        user_id="u-first",
        username="repeat-user",
        provider=_PROVIDER,
        subject="oid-repeat",
    )
    second = repo.create_user_with_identity(
        user_id="u-second",
        username="repeat-user",
        provider=_PROVIDER,
        subject="oid-repeat",
    )
    assert first == second == "u-first"
    assert _count(session_local, User) == 1
    assert _count(session_local, OAuthIdentity) == 1


def test_username_collision_creates_a_new_user_and_does_not_link_local(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
) -> None:
    repo, session_local = oauth_repo
    seed_test_users(session_local, "u-local")
    with session_local() as session:
        local = session.execute(select(User).where(User.id == "u-local")).scalar_one()
        local.username = "alice"
        session.add(WatchlistItem(user_id="u-local", stock_id="US:AAPL"))
        session.commit()

    entra_id = repo.create_user_with_identity(
        user_id="u-entra",
        username="alice",
        provider=_PROVIDER,
        subject="oid-alice",
    )
    assert entra_id == "u-entra"
    assert entra_id != "u-local"
    with session_local() as session:
        local = session.execute(select(User).where(User.id == "u-local")).scalar_one()
        entra = session.execute(select(User).where(User.id == "u-entra")).scalar_one()
        watchlist = session.execute(
            select(WatchlistItem).where(WatchlistItem.user_id == "u-local"),
        ).scalars().all()
        assert local.username == "alice"
        assert entra.username == "alice-1"
        assert len(watchlist) == 1
        assert watchlist[0].stock_id == "US:AAPL"
        assert repo.get_user_id(provider=_PROVIDER, subject="oid-alice") == "u-entra"


def test_forty_eight_char_name_plus_suffix_is_stored_unchanged(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
) -> None:
    repo, session_local = oauth_repo
    base = "a" * 48
    chosen = f"{base}-9"
    with session_local() as session:
        session.add(User(id="u-local-long", username=base, password_hash="hash"))
        session.commit()

    entra_id = repo.create_user_with_identity(
        user_id="u-ninth",
        username=chosen,
        provider=_PROVIDER,
        subject="oid-ninth",
    )
    assert entra_id == "u-ninth"
    with session_local() as session:
        local = session.execute(select(User).where(User.id == "u-local-long")).scalar_one()
        entra = session.execute(select(User).where(User.id == "u-ninth")).scalar_one()
        assert local.username == base
        assert local.password_hash == "hash"
        assert entra.username == chosen


def test_username_retry_reaches_the_ninth_suffix(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
) -> None:
    repo, session_local = oauth_repo
    stem = "n" * 48
    taken = [stem, *[f"{stem}-{index}" for index in range(1, 9)]]
    with session_local() as session:
        for index, name in enumerate(taken):
            session.add(User(id=f"u-taken-{index}", username=name, password_hash=None))
        session.commit()

    repo.create_user_with_identity(
        user_id="u-next",
        username=stem,
        provider=_PROVIDER,
        subject="oid-next",
    )
    with session_local() as session:
        created = session.execute(select(User).where(User.id == "u-next")).scalar_one()
    assert created.username == f"{stem}-9"


def test_failed_identity_insert_leaves_no_orphan_user(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, session_local = oauth_repo
    original_flush = Session.flush

    def boom(self, *args, **kwargs):
        original_flush(self, *args, **kwargs)
        raise IntegrityError("INSERT", {}, Exception("forced failure after users flush"))

    monkeypatch.setattr(Session, "flush", boom)
    with pytest.raises(OAuthIdentityPersistError):
        repo.create_user_with_identity(
            user_id="u-orphan",
            username="orphan-user",
            provider=_PROVIDER,
            subject="oid-orphan",
        )
    assert _count(session_local, User) == 0
    assert _count(session_local, OAuthIdentity) == 0


def test_persist_error_logs_no_identity_details(
    oauth_repo: tuple[OAuthIdentityRepository, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo, _session_local = oauth_repo

    def boom(self, *args, **kwargs):
        raise IntegrityError(
            "INSERT",
            {},
            Exception(
                'insert or update on table "oauth_identities" violates foreign key '
                'constraint "oauth_identities_user_id_fkey"\n'
                "DETAIL: Key (user_id)=(u-secret) is not present. "
                "subject secret-oid username secret.user@example.com"
            ),
        )

    monkeypatch.setattr(Session, "flush", boom)
    caplog.set_level(logging.ERROR)
    with pytest.raises(OAuthIdentityPersistError):
        repo.create_user_with_identity(
            user_id="u-secret",
            username="secret.user@example.com",
            provider=_PROVIDER,
            subject="secret-oid",
        )
    blob = "\n".join(record.getMessage() for record in caplog.records)
    blob += "\n".join(repr(record.__dict__) for record in caplog.records)
    assert "secret-oid" not in blob
    assert "secret.user@example.com" not in blob
    assert "u-secret" not in blob
    assert "Failed to persist Entra identity" in blob
    assert caplog.records[-1].constraint_name == "oauth_identities_user_id_fkey"
