from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal

_UNSET = object()
ModelT = TypeVar("ModelT")


@dataclass(frozen=True)
class DailyUpsertStats:
    inserted: int
    updated: int


def database_is_configured() -> bool:
    return SessionLocal is not None


def require_session_factory() -> sessionmaker[Session]:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    return SessionLocal


class SessionBoundRepository:
    """Shared session-factory override helpers used by persisted repositories."""

    def __init__(self) -> None:
        self._session_factory_override = _UNSET

    def set_session_factory_override(self, session_factory) -> None:
        self._session_factory_override = session_factory

    def is_available(self) -> bool:
        if self._session_factory_override is not _UNSET:
            return self._session_factory_override is not None
        return database_is_configured()

    def _session_factory(self):
        if self._session_factory_override is not _UNSET:
            if self._session_factory_override is None:
                raise RuntimeError("DATABASE_URL is not configured")
            return self._session_factory_override
        return require_session_factory()


def upsert_market_symbol_date_rows(
    session_factory: sessionmaker[Session],
    *,
    model: type[ModelT],
    market: str,
    source: str,
    records: list[Any],
    build_row: Callable[[Any, date], ModelT],
    apply_update: Callable[[ModelT, Any, str], None],
) -> DailyUpsertStats:
    """Insert-or-update rows keyed by UNIQUE(market, symbol, date)."""
    if not records:
        return DailyUpsertStats(inserted=0, updated=0)

    inserted = 0
    updated = 0
    with session_factory() as session:
        for record in records:
            trade_date = date.fromisoformat(record.date)
            row = build_row(record, trade_date)
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
                inserted += 1
            except IntegrityError:
                existing = session.execute(
                    select(model).where(
                        model.market == market,
                        model.symbol == record.symbol,
                        model.date == trade_date,
                    )
                ).scalar_one()
                apply_update(existing, record, source)
                updated += 1
        session.commit()
    return DailyUpsertStats(inserted=inserted, updated=updated)


def list_market_symbol_history(
    session_factory: sessionmaker[Session],
    *,
    model: type[ModelT],
    market: str,
    symbol: str,
    limit: int,
) -> list[ModelT]:
    with session_factory() as session:
        return list(
            session.execute(
                select(model)
                .where(
                    model.market == market,
                    model.symbol == symbol,
                )
                .order_by(model.date.desc())
                .limit(max(limit, 1))
            ).scalars()
        )
