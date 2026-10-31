"""Persist Entra (and future) OAuth subject ↔ local users.id links (#240, #301)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.constraint_errors import (
    is_oauth_identity_unique_violation,
    is_username_unique_violation,
    safe_constraint_name,
)
from app.db.models import OAuthIdentity, User
from app.repositories.session import database_is_configured, require_session_factory

logger = logging.getLogger(__name__)

_USERNAME_MAX_LENGTH = 64
# Races can take the name chosen by the caller. Keep going past a handful of
# suffixes so a long sanitized name plus "-9" is stored as given (#301).
_MAX_USERNAME_ATTEMPTS = 32


class OAuthIdentityPersistError(Exception):
    """Identity write failed. Callers must not leak database details."""


class OAuthIdentityRepository:
    def is_available(self) -> bool:
        return database_is_configured()

    def get_user_id(self, *, provider: str, subject: str) -> str | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            return self._lookup_user_id(session, provider=provider, subject=subject)

    def create_user_with_identity(
        self,
        *,
        user_id: str,
        username: str,
        provider: str,
        subject: str,
    ) -> str:
        """Create a passwordless local user and link the OAuth subject.

        Inserts ``users`` before ``oauth_identities`` in one transaction (#301).
        Never links an Entra subject onto an existing local username.
        """
        session_factory = require_session_factory()
        for candidate in _username_candidates(username):
            with session_factory() as session:
                try:
                    user = User(
                        id=user_id,
                        username=candidate,
                        password_hash=None,
                    )
                    session.add(user)
                    # Flush so PostgreSQL sees the parent row before the FK insert.
                    session.flush()
                    user.oauth_identities.append(
                        OAuthIdentity(
                            provider=provider,
                            subject=subject,
                        )
                    )
                    session.commit()
                    return user_id
                except IntegrityError as exc:
                    session.rollback()
                    existing = self.get_user_id(provider=provider, subject=subject)
                    if existing is not None:
                        return existing
                    if is_username_unique_violation(exc):
                        continue
                    _log_persist_failure(
                        "identity" if is_oauth_identity_unique_violation(exc) else "other",
                        exc,
                    )
                    raise OAuthIdentityPersistError() from None
                except SQLAlchemyError as exc:
                    session.rollback()
                    _log_persist_failure("other", exc)
                    raise OAuthIdentityPersistError() from None
        _log_persist_failure("username")
        raise OAuthIdentityPersistError()

    @staticmethod
    def _lookup_user_id(session, *, provider: str, subject: str) -> str | None:
        row = session.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == provider,
                OAuthIdentity.subject == subject,
            ),
        ).scalar_one_or_none()
        return None if row is None else str(row.user_id)


def _username_candidates(username: str) -> list[str]:
    """First try the caller's name unchanged (up to 64 chars), then suffixes.

    A 48-character base plus ``-9`` must stay intact. Cutting it back to 48
    characters retries names that already belong to someone else (#301).
    """
    chosen = (username.strip() or "entra-user")[:_USERNAME_MAX_LENGTH]
    candidates = [chosen]
    for attempt in range(1, _MAX_USERNAME_ATTEMPTS):
        suffix = f"-{attempt}"
        room = max(1, _USERNAME_MAX_LENGTH - len(suffix))
        candidates.append(f"{chosen[:room]}{suffix}")
    return candidates


def _log_persist_failure(constraint_kind: str, exc: BaseException | None = None) -> None:
    logger.error(
        "Failed to persist Entra identity",
        extra={
            "event": "auth.entra.identity_persist_failed",
            "constraint_kind": constraint_kind,
            "constraint_name": safe_constraint_name(exc),
        },
    )


oauth_identity_repository = OAuthIdentityRepository()
