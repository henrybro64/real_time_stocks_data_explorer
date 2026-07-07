from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from app.repositories.user_ai_preference_repository import (
    user_ai_preference_repository,
)
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
        try:
            user_ai_preference_repository.upsert(user_id, normalized)
            return normalized
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "AI preference storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def _get_row(self, user_id: str):
        return user_ai_preference_repository.get_by_user_id(user_id)


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PREFERRED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized


user_ai_preference_service = UserAiPreferenceService()
