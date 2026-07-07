from sqlalchemy import select

from app.db.models import User

from .session import database_is_configured, require_session_factory


class UserRepository:
    def is_available(self) -> bool:
        return database_is_configured()

    def get_by_id(self, user_id: str) -> User | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            return session.execute(
                select(User).where(User.id == user_id),
            ).scalar_one_or_none()

    def get_by_username(self, username: str) -> User | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            return session.execute(
                select(User).where(User.username == username),
            ).scalar_one_or_none()

    def create_local_user(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
    ) -> User:
        session_factory = require_session_factory()
        with session_factory() as session:
            row = User(
                id=user_id,
                username=username,
                password_hash=password_hash,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def upsert_local_user_password(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
    ) -> User:
        session_factory = require_session_factory()
        with session_factory() as session:
            row = session.execute(
                select(User).where(User.username == username),
            ).scalar_one_or_none()
            if row is None:
                row = User(
                    id=user_id,
                    username=username,
                    password_hash=password_hash,
                )
                session.add(row)
            else:
                row.password_hash = password_hash
            session.commit()
            session.refresh(row)
            return row


user_repository = UserRepository()
