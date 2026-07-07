from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal


def database_is_configured() -> bool:
    return SessionLocal is not None


def require_session_factory() -> sessionmaker[Session]:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    return SessionLocal

