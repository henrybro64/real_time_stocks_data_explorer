"""Persist OAuth users and identities in Postgres."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import OAuthIdentity, User
from app.db.session import SessionLocal


def find_or_create_oauth_user(provider: str, subject: str, email: str | None) -> str:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    key_provider = provider.lower().strip()
    with SessionLocal() as session:
        link = session.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == key_provider,
                OAuthIdentity.subject == subject,
            ),
        ).scalar_one_or_none()
        if link is not None:
            return link.user_id

        username = _pick_username(session, email, key_provider, subject)
        uid = f"u-{uuid.uuid4().hex[:16]}"
        session.add(User(id=uid, username=username))
        session.add(
            OAuthIdentity(
                user_id=uid,
                provider=key_provider,
                subject=subject,
            ),
        )
        session.commit()
        return uid


def _pick_username(session, email: str | None, provider: str, subject: str) -> str:
    base = (email or "").split("@")[0].strip() if email else ""
    if not base:
        base = f"{provider}-{subject[:8]}"
    base = base[:64]
    candidate = base
    n = 0
    while True:
        exists = session.execute(
            select(User.id).where(User.username == candidate),
        ).scalar_one_or_none()
        if exists is None:
            return candidate
        n += 1
        suffix = f"-{n}"
        candidate = f"{base[: max(1, 64 - len(suffix))]}{suffix}"


def get_user_public(user_id: str) -> dict[str, str] | None:
    if SessionLocal is None:
        return None
    with SessionLocal() as session:
        user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if user is None:
            return None
        return {"username": user.username}
