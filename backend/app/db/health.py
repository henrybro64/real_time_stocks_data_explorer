from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal


def validate_database_connection() -> None:
    """Validate that Postgres is reachable and can execute a query."""
    if SessionLocal is None:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Set DATABASE_URL "
            "(e.g. postgresql+psycopg://user:pass@host:5432/dbname).",
        )

    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as exc:
        raise RuntimeError(
            "Failed to connect to Postgres using DATABASE_URL. "
            "Ensure Postgres is running and credentials are correct.",
        ) from exc
