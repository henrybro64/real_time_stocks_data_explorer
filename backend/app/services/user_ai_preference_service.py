from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import UserAiPreference
from app.db.session import SessionLocal
from app.services.api_key_service import SUPPORTED_PROVIDERS

SUPPORTED_PREFERRED_PROVIDERS = SUPPORTED_PROVIDERS


class UserAiPreferenceService:
    def get_preferred_provider(self, user_id: str) -> str | None:
        try:
            row = self._get_row(user_id)
            return row.preferred_provider if row is not None else None
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "AI preference storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def set_preferred_provider(self, user_id: str, provider: str) -> str:
        normalized = _normalize_provider(provider)
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")

        try:
            with SessionLocal() as session:
                row = session.execute(
                    select(UserAiPreference).where(UserAiPreference.user_id == user_id),
                ).scalar_one_or_none()
                if row is None:
                    row = UserAiPreference(
                        user_id=user_id,
                        preferred_provider=normalized,
                    )
                    session.add(row)
                else:
                    row.preferred_provider = normalized
                session.commit()
                return normalized
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "AI preference storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def _get_row(self, user_id: str) -> UserAiPreference | None:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            return session.execute(
                select(UserAiPreference).where(UserAiPreference.user_id == user_id),
            ).scalar_one_or_none()


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PREFERRED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized


user_ai_preference_service = UserAiPreferenceService()
