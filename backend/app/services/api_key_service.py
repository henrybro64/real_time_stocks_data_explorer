from sqlalchemy.exc import SQLAlchemyError

from app.repositories.api_key_repository import api_key_repository

SUPPORTED_PROVIDERS = {"openai", "perplexity"}


class ApiKeyService:
    def get_status(self, user_id: str, provider: str) -> tuple[bool, str | None]:
        try:
            row = self._get_row(user_id, provider)
            if row is None:
                return False, None
            masked = row.api_key[-4:] if len(row.api_key) >= 4 else row.api_key
            return True, masked
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "API key storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def get_key(self, user_id: str, provider: str) -> str | None:
        try:
            row = self._get_row(user_id, provider)
            return row.api_key if row is not None else None
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "API key storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def upsert_key(
        self,
        user_id: str,
        provider: str,
        api_key: str,
    ) -> tuple[bool, str | None]:
        provider = self._normalize_provider(provider)
        cleaned = api_key.strip()
        if not cleaned:
            raise ValueError("API key cannot be empty")

        try:
            api_key_repository.upsert(user_id, provider, cleaned)
            masked = cleaned[-4:] if len(cleaned) >= 4 else cleaned
            return True, masked
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "API key storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def _get_row(self, user_id: str, provider: str):
        provider = self._normalize_provider(provider)
        return api_key_repository.get_by_user_and_provider(user_id, provider)

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        return normalized


api_key_service = ApiKeyService()
