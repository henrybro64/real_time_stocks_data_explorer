from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.init_db import DEFAULT_USER_ID
from app.db.models import UserApiKey
from app.db.session import SessionLocal

SUPPORTED_PROVIDERS = {"openai", "perplexity"}


class ApiKeyService:
    def get_status(self, provider: str) -> tuple[bool, str | None]:
        try:
            row = self._get_row(provider)
            if row is None:
                return False, None
            masked = row.api_key[-4:] if len(row.api_key) >= 4 else row.api_key
            return True, masked
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "API key storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def get_key(self, provider: str) -> str | None:
        try:
            row = self._get_row(provider)
            return row.api_key if row is not None else None
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "API key storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def upsert_key(self, provider: str, api_key: str) -> tuple[bool, str | None]:
        provider = self._normalize_provider(provider)
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")

        cleaned = api_key.strip()
        if not cleaned:
            raise ValueError("API key cannot be empty")

        try:
            with SessionLocal() as session:
                row = session.execute(
                    select(UserApiKey).where(
                        UserApiKey.user_id == DEFAULT_USER_ID,
                        UserApiKey.provider == provider,
                    ),
                ).scalar_one_or_none()
                if row is None:
                    row = UserApiKey(
                        user_id=DEFAULT_USER_ID,
                        provider=provider,
                        api_key=cleaned,
                    )
                    session.add(row)
                else:
                    row.api_key = cleaned
                session.commit()
                masked = cleaned[-4:] if len(cleaned) >= 4 else cleaned
                return True, masked
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "API key storage schema is missing. Run `alembic upgrade head`.",
            ) from exc

    def _get_row(self, provider: str) -> UserApiKey | None:
        provider = self._normalize_provider(provider)
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            return session.execute(
                select(UserApiKey).where(
                    UserApiKey.user_id == DEFAULT_USER_ID,
                    UserApiKey.provider == provider,
                ),
            ).scalar_one_or_none()

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        return normalized


api_key_service = ApiKeyService()
