"""Local username/password account management."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid

from app.repositories.user_repository import user_repository


def _hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        120_000,
    )
    return f"pbkdf2_sha256$120000${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds_s, salt_hex, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        rounds,
    ).hex()
    return hmac.compare_digest(candidate, digest_hex)


def get_user_by_id(user_id: str) -> dict[str, str] | None:
    if not user_repository.is_available():
        return None
    row = user_repository.get_by_id(user_id)
    if row is None:
        return None
    return {"id": row.id, "username": row.username}


def verify_local_login(username: str, password: str) -> str | None:
    if not user_repository.is_available():
        raise RuntimeError("DATABASE_URL is not configured")
    row = user_repository.get_by_username(username)
    if row is None or not row.password_hash:
        return None
    if not _verify_password(password, row.password_hash):
        return None
    return row.id


def register_local_user(username: str, password: str) -> str:
    from sqlalchemy.exc import IntegrityError

    if not user_repository.is_available():
        raise RuntimeError("DATABASE_URL is not configured")
    uname = username.strip()
    if not uname:
        raise ValueError("Username is required")
    if len(uname) > 64:
        raise ValueError("Username must be at most 64 characters")
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    existing = user_repository.get_by_username(uname)
    if existing is not None:
        raise ValueError("Username already exists")
    try:
        user = user_repository.create_local_user(
            user_id=f"u-{uuid.uuid4().hex[:16]}",
            username=uname,
            password_hash=_hash_password(password),
        )
    except IntegrityError as exc:
        raise ValueError("Username already exists") from exc
    return user.id


def ensure_bootstrap_local_user(username: str, password: str) -> None:
    """Create or reset a configured bootstrap local user account."""
    if not user_repository.is_available():
        return
    uname = username.strip()
    if not uname or not password:
        return
    user_repository.upsert_local_user_password(
        user_id=f"u-{uuid.uuid4().hex[:16]}",
        username=uname[:64],
        password_hash=_hash_password(password),
    )
