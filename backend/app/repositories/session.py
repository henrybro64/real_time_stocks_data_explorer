from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypeVar

from sqlalchemy import func, select, tuple_
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


def persist_new(session: Session, entity: ModelT) -> ModelT:
    session.add(entity)
    session.commit()
    session.refresh(entity)
    return entity


def apply_field_updates(session: Session, entity: ModelT, updates: dict[str, object]) -> ModelT:
    for field, value in updates.items():
        setattr(entity, field, value)
    session.commit()
    session.refresh(entity)
    return entity


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


_UPSERT_CHUNK = 400


def _dialect_insert(session: Session):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        return insert
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        return insert
    return None


def _row_payload(row: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.primary_key:
            continue
        value = getattr(row, column.key)
        if value is None and column.key in {"created_at", "updated_at"}:
            continue
        payload[column.key] = value
    return payload


def upsert_market_symbol_date_rows(
    session_factory: sessionmaker[Session],
    *,
    model: type[ModelT],
    market: str,
    source: str,
    records: list[Any],
    build_row: Callable[[Any, date], ModelT],
    apply_update: Callable[[ModelT, Any, str], None],
    preserve_existing_when_null: frozenset[str] | None = None,
) -> DailyUpsertStats:
    """Insert-or-update rows keyed by UNIQUE(market, symbol, date).

    One ``INSERT … ON CONFLICT`` per chunk, not one statement per row (#274).
    """
    if not records:
        return DailyUpsertStats(inserted=0, updated=0)

    preserve = preserve_existing_when_null or frozenset()
    inserted = 0
    updated = 0
    with session_factory() as session:
        insert = _dialect_insert(session)
        if insert is None:
            return _upsert_market_symbol_date_rows_individually(
                session,
                model=model,
                market=market,
                source=source,
                records=records,
                build_row=build_row,
                apply_update=apply_update,
            )
        for start in range(0, len(records), _UPSERT_CHUNK):
            chunk = records[start : start + _UPSERT_CHUNK]
            deduped: dict[tuple[str, date], dict[str, Any]] = {}
            for record in chunk:
                trade_date = date.fromisoformat(record.date)
                payload = _row_payload(build_row(record, trade_date))
                deduped[(str(payload["symbol"]), trade_date)] = payload
            payloads = list(deduped.values())
            pairs = [(payload["symbol"], payload["date"]) for payload in payloads]
            existing = set(
                session.execute(
                    select(model.symbol, model.date).where(
                        model.market == market,
                        tuple_(model.symbol, model.date).in_(pairs),
                    )
                ).all()
            )
            stmt = insert(model).values(payloads)
            update_keys = [
                key
                for key in payloads[0]
                if key not in {"market", "symbol", "date", "created_at"}
            ]
            table = model.__table__
            assignments = {}
            for key in update_keys:
                if key in preserve:
                    assignments[key] = func.coalesce(stmt.excluded[key], table.c[key])
                else:
                    assignments[key] = stmt.excluded[key]
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["market", "symbol", "date"],
                    set_=assignments,
                )
            )
            for payload in payloads:
                key = (payload["symbol"], payload["date"])
                if key in existing:
                    updated += 1
                else:
                    inserted += 1
        session.commit()
    return DailyUpsertStats(inserted=inserted, updated=updated)


def _upsert_market_symbol_date_rows_individually(
    session: Session,
    *,
    model: type[ModelT],
    market: str,
    source: str,
    records: list[Any],
    build_row: Callable[[Any, date], ModelT],
    apply_update: Callable[[ModelT, Any, str], None],
) -> DailyUpsertStats:
    inserted = 0
    updated = 0
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
