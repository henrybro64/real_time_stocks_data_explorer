"""Bootstrap local user is create-only by default (#286)."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from app.services.local_auth_service import ensure_bootstrap_local_user


class _FakeUsers:
    def __init__(self) -> None:
        self.by_username: dict[str, SimpleNamespace] = {}
        self.creates: list[tuple[str, str, str]] = []
        self.upserts: list[tuple[str, str, str]] = []

    def is_available(self) -> bool:
        return True

    def get_by_username(self, username: str):
        return self.by_username.get(username)

    def create_local_user(self, *, user_id: str, username: str, password_hash: str):
        row = SimpleNamespace(id=user_id, username=username, password_hash=password_hash)
        self.by_username[username] = row
        self.creates.append((user_id, username, password_hash))
        return row

    def upsert_local_user_password(self, *, user_id: str, username: str, password_hash: str):
        row = self.by_username.get(username)
        if row is None:
            row = SimpleNamespace(id=user_id, username=username, password_hash=password_hash)
            self.by_username[username] = row
        else:
            row.password_hash = password_hash
        self.upserts.append((user_id, username, password_hash))
        return row


def test_bootstrap_creates_missing_user(monkeypatch) -> None:
    from app.services import local_auth_service as svc

    fake = _FakeUsers()
    monkeypatch.setattr(svc, "user_repository", fake)
    assert ensure_bootstrap_local_user("admin", "secure-password-1") == "created"
    assert len(fake.creates) == 1
    assert fake.upserts == []


def test_bootstrap_leaves_existing_password_unchanged(monkeypatch) -> None:
    from app.services import local_auth_service as svc

    fake = _FakeUsers()
    fake.by_username["admin"] = SimpleNamespace(
        id="u-1",
        username="admin",
        password_hash="existing-hash",
    )
    monkeypatch.setattr(svc, "user_repository", fake)
    assert ensure_bootstrap_local_user("admin", "brand-new-password") == "unchanged"
    assert fake.creates == []
    assert fake.upserts == []
    assert fake.by_username["admin"].password_hash == "existing-hash"


def test_bootstrap_create_race_does_not_overwrite_the_winner(monkeypatch) -> None:
    from app.services import local_auth_service as svc

    fake = _FakeUsers()

    def create_local_user(*, user_id: str, username: str, password_hash: str):
        fake.by_username[username] = SimpleNamespace(
            id="u-winner",
            username=username,
            password_hash="winner-hash",
        )
        raise IntegrityError(
            "INSERT",
            {},
            Exception('duplicate key value violates unique constraint "users_username_key"'),
        )

    fake.create_local_user = create_local_user  # type: ignore[method-assign]
    monkeypatch.setattr(svc, "user_repository", fake)
    assert ensure_bootstrap_local_user("admin", "brand-new-password") == "unchanged"
    assert fake.upserts == []
    assert fake.by_username["admin"].password_hash == "winner-hash"


def test_bootstrap_explicit_reset_overwrites_password(monkeypatch) -> None:
    from app.services import local_auth_service as svc

    fake = _FakeUsers()
    fake.by_username["admin"] = SimpleNamespace(
        id="u-1",
        username="admin",
        password_hash="existing-hash",
    )
    monkeypatch.setattr(svc, "user_repository", fake)
    assert (
        ensure_bootstrap_local_user(
            "admin",
            "brand-new-password",
            reset_password=True,
        )
        == "reset"
    )
    assert len(fake.upserts) == 1
    assert fake.by_username["admin"].password_hash != "existing-hash"
