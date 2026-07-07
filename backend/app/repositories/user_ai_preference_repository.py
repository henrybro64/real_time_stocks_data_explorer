from sqlalchemy import select

from app.db.models import UserAiPreference

from .session import require_session_factory


class UserAiPreferenceRepository:
    def get_by_user_id(self, user_id: str) -> UserAiPreference | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            return session.execute(
                select(UserAiPreference).where(UserAiPreference.user_id == user_id),
            ).scalar_one_or_none()

    def upsert(
        self,
        user_id: str,
        preferred_provider: str,
    ) -> UserAiPreference:
        session_factory = require_session_factory()
        with session_factory() as session:
            row = session.execute(
                select(UserAiPreference).where(UserAiPreference.user_id == user_id),
            ).scalar_one_or_none()
            if row is None:
                row = UserAiPreference(
                    user_id=user_id,
                    preferred_provider=preferred_provider,
                )
                session.add(row)
            else:
                row.preferred_provider = preferred_provider
            session.commit()
            session.refresh(row)
            return row


user_ai_preference_repository = UserAiPreferenceRepository()

