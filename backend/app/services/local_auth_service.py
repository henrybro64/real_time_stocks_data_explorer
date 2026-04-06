"""Local username/password account management."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid

from sqlalchemy import select

from app.db.models import User
from app.db.session import SessionLocal


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
    if SessionLocal is None:
        return None
    with SessionLocal() as session:
        row = session.execute(
            select(User).where(User.id == user_id),
        ).scalar_one_or_none()
        if row is None:
            return None
        return {"id": row.id, "username": row.username}


def verify_local_login(username: str, password: str) -> str | None:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    with SessionLocal() as session:
        row = session.execute(
            select(User).where(User.username == username),
        ).scalar_one_or_none()
        if row is None or not row.password_hash:
            return None
        if not _verify_password(password, row.password_hash):
            return None
        return row.id


def register_local_user(username: str, password: str) -> str:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    uname = username.strip()
    if not uname:
        raise ValueError("Username is required")
    with SessionLocal() as session:
        existing = session.execute(
            select(User).where(User.username == uname),
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("Username already exists")
        user = User(
            id=f"u-{uuid.uuid4().hex[:16]}",
            username=uname[:64],
            password_hash=_hash_password(password),
        )
        session.add(user)
        session.commit()
        return user.id


def ensure_bootstrap_local_user(username: str, password: str) -> None:
    """Create or reset a configured bootstrap local user account."""
    if SessionLocal is None:
        return
    uname = username.strip()
    if not uname or not password:
        return
    with SessionLocal() as session:
        row = session.execute(
            select(User).where(User.username == uname),
        ).scalar_one_or_none()
        if row is None:
            row = User(
                id=f"u-{uuid.uuid4().hex[:16]}",
                username=uname[:64],
                password_hash=_hash_password(password),
            )
            session.add(row)
        else:
            row.password_hash = _hash_password(password)
        session.commit()
