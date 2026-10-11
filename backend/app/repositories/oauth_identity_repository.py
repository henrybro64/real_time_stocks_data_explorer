"""Persist Entra (and future) OAuth subject ↔ local users.id links (#240)."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import OAuthIdentity, User
from app.repositories.session import database_is_configured, require_session_factory


class OAuthIdentityRepository:
    def is_available(self) -> bool:
        return database_is_configured()

    def get_user_id(self, *, provider: str, subject: str) -> str | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            row = session.execute(
                select(OAuthIdentity).where(
                    OAuthIdentity.provider == provider,
                    OAuthIdentity.subject == subject,
                ),
            ).scalar_one_or_none()
            return None if row is None else str(row.user_id)

    def create_user_with_identity(
        self,
        *,
        user_id: str,
        username: str,
        provider: str,
        subject: str,
    ) -> str:
        """Create a passwordless local user and link the OAuth subject."""
        session_factory = require_session_factory()
        with session_factory() as session:
            session.add(
                User(
                    id=user_id,
                    username=username,
                    password_hash=None,
                )
            )
            session.add(
                OAuthIdentity(
                    user_id=user_id,
                    provider=provider,
                    subject=subject,
                )
            )
            session.commit()
        return user_id


oauth_identity_repository = OAuthIdentityRepository()
