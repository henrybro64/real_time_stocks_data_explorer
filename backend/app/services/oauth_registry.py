"""In-memory OAuth user registry (MVP). Replace with Postgres for production."""

from __future__ import annotations

import uuid
_identity_to_user: dict[str, str] = {}
_users: dict[str, dict[str, str]] = {}
_usernames: set[str] = set()


def _identity_key(provider: str, subject: str) -> str:
    return f"{provider}:{subject}"


def find_or_create_user(
    provider: str,
    subject: str,
    email: str | None,
) -> str:
    key = _identity_key(provider, subject)
    if key in _identity_to_user:
        return _identity_to_user[key]

    uid = f"u-{uuid.uuid4().hex[:16]}"
    _identity_to_user[key] = uid

    base = (email or "").split("@")[0].strip() if email else ""
    if not base:
        base = f"{provider}-{subject[:8]}"
    username = base[:64]
    n = 0
    while username in _usernames:
        n += 1
        suffix = f"-{n}"
        username = f"{base[: max(1, 64 - len(suffix))]}{suffix}"
    _usernames.add(username)
    _users[uid] = {"username": username, "provider": provider}
    return uid


def get_public_user(user_id: str) -> dict[str, str] | None:
    return _users.get(user_id)


def reset_for_tests() -> None:
    _identity_to_user.clear()
    _users.clear()
    _usernames.clear()
