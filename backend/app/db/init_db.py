from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.init_ids import DEFAULT_USER_ID
from app.db.models import User
from app.db.session import SessionLocal


def seed_default_user() -> None:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    try:
        with SessionLocal() as session:
            existing = session.execute(
                select(User).where(User.id == DEFAULT_USER_ID),
            ).scalar_one_or_none()
            if existing is None:
                session.add(User(id=DEFAULT_USER_ID, username=DEFAULT_USER_ID))
                session.commit()
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "Database schema is missing or incompatible. "
            "Run `alembic upgrade head` before starting the app.",
        ) from exc
