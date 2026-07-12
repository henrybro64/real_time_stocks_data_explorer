from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import TaiwanMarginShortDaily
from app.services.tw_margin_short_service import TaiwanMarginShortRecord

from .session import database_is_configured, require_session_factory

_UNSET = object()


@dataclass(frozen=True)
class MarginShortDailyUpsertStats:
    inserted: int
    updated: int


class TaiwanMarginShortDailyRepository:
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

    def upsert_records(
        self,
        *,
        market: str,
        source: str,
        records: list[TaiwanMarginShortRecord],
    ) -> MarginShortDailyUpsertStats:
        if not records:
            return MarginShortDailyUpsertStats(inserted=0, updated=0)

        session_factory = self._session_factory()
        inserted = 0
        updated = 0
        with session_factory() as session:
            for record in records:
                trade_date = date.fromisoformat(record.date)
                row = TaiwanMarginShortDaily(
                    market=market,
                    symbol=record.symbol,
                    date=trade_date,
                    name=record.name or None,
                    close_price=record.close_price,
                    margin_balance_lots=record.margin_balance_lots,
                    short_margin_balance_lots=record.short_margin_balance_lots,
                    borrowed_short_balance_lots=record.borrowed_short_balance_lots,
                    borrowed_short_balance_change_lots=record.borrowed_short_balance_change_lots,
                    borrowed_short_balance_change_amount_10k=(
                        record.borrowed_short_balance_change_amount_10k
                    ),
                    source=source,
                )
                try:
                    with session.begin_nested():
                        session.add(row)
                        session.flush()
                    inserted += 1
                except IntegrityError:
                    existing = session.execute(
                        select(TaiwanMarginShortDaily).where(
                            TaiwanMarginShortDaily.market == market,
                            TaiwanMarginShortDaily.symbol == record.symbol,
                            TaiwanMarginShortDaily.date == trade_date,
                        )
                    ).scalar_one()
                    existing.name = record.name or None
                    existing.close_price = record.close_price
                    existing.margin_balance_lots = record.margin_balance_lots
                    existing.short_margin_balance_lots = record.short_margin_balance_lots
                    existing.borrowed_short_balance_lots = record.borrowed_short_balance_lots
                    existing.borrowed_short_balance_change_lots = (
                        record.borrowed_short_balance_change_lots
                    )
                    existing.borrowed_short_balance_change_amount_10k = (
                        record.borrowed_short_balance_change_amount_10k
                    )
                    existing.source = source
                    updated += 1
            session.commit()
        return MarginShortDailyUpsertStats(inserted=inserted, updated=updated)

    def list_history(
        self,
        *,
        market: str,
        symbol: str,
        limit: int,
    ) -> list[TaiwanMarginShortDaily]:
        session_factory = self._session_factory()
        with session_factory() as session:
            return list(
                session.execute(
                    select(TaiwanMarginShortDaily)
                    .where(
                        TaiwanMarginShortDaily.market == market,
                        TaiwanMarginShortDaily.symbol == symbol,
                    )
                    .order_by(TaiwanMarginShortDaily.date.desc())
                    .limit(max(limit, 1))
                ).scalars()
            )


taiwan_margin_short_daily_repository = TaiwanMarginShortDailyRepository()
