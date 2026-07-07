from sqlalchemy import select

from app.db.models import UserApiKey

from .session import require_session_factory


class ApiKeyRepository:
    def get_by_user_and_provider(
        self,
        user_id: str,
        provider: str,
    ) -> UserApiKey | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            return session.execute(
                select(UserApiKey).where(
                    UserApiKey.user_id == user_id,
                    UserApiKey.provider == provider,
                ),
            ).scalar_one_or_none()

    def upsert(
        self,
        user_id: str,
        provider: str,
        api_key: str,
    ) -> UserApiKey:
        session_factory = require_session_factory()
        with session_factory() as session:
            row = session.execute(
                select(UserApiKey).where(
                    UserApiKey.user_id == user_id,
                    UserApiKey.provider == provider,
                ),
            ).scalar_one_or_none()
            if row is None:
                row = UserApiKey(
                    user_id=user_id,
                    provider=provider,
                    api_key=api_key,
                )
                session.add(row)
            else:
                row.api_key = api_key
            session.commit()
            session.refresh(row)
            return row


api_key_repository = ApiKeyRepository()

