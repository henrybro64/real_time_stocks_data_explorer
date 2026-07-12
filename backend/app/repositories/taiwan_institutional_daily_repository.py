from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import TaiwanInstitutionalDaily
from app.services.tw_institutional_service import TaiwanInstitutionalInvestorRecord

from .session import database_is_configured, require_session_factory

_UNSET = object()


@dataclass(frozen=True)
class InstitutionalDailyUpsertStats:
    inserted: int
    updated: int


class TaiwanInstitutionalDailyRepository:
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
        records: list[TaiwanInstitutionalInvestorRecord],
    ) -> InstitutionalDailyUpsertStats:
        if not records:
            return InstitutionalDailyUpsertStats(inserted=0, updated=0)

        session_factory = self._session_factory()
        inserted = 0
        updated = 0
        with session_factory() as session:
            for record in records:
                trade_date = date.fromisoformat(record.date)
                row = TaiwanInstitutionalDaily(
                    market=market,
                    symbol=record.symbol,
                    date=trade_date,
                    name=record.name or None,
                    foreign_net_shares=record.foreign_net_shares,
                    investment_trust_net_shares=record.investment_trust_net_shares,
                    dealer_self_net_shares=record.dealer_self_net_shares,
                    dealer_hedge_net_shares=record.dealer_hedge_net_shares,
                    dealer_net_shares=record.dealer_net_shares,
                    total_net_shares=record.total_net_shares,
                    source=source,
                )
                try:
                    with session.begin_nested():
                        session.add(row)
                        session.flush()
                    inserted += 1
                except IntegrityError:
                    existing = session.execute(
                        select(TaiwanInstitutionalDaily).where(
                            TaiwanInstitutionalDaily.market == market,
                            TaiwanInstitutionalDaily.symbol == record.symbol,
                            TaiwanInstitutionalDaily.date == trade_date,
                        )
                    ).scalar_one()
                    existing.name = record.name or None
                    existing.foreign_net_shares = record.foreign_net_shares
                    existing.investment_trust_net_shares = record.investment_trust_net_shares
                    existing.dealer_self_net_shares = record.dealer_self_net_shares
                    existing.dealer_hedge_net_shares = record.dealer_hedge_net_shares
                    existing.dealer_net_shares = record.dealer_net_shares
                    existing.total_net_shares = record.total_net_shares
                    existing.source = source
                    updated += 1
            session.commit()
        return InstitutionalDailyUpsertStats(inserted=inserted, updated=updated)

    def list_history(
        self,
        *,
        market: str,
        symbol: str,
        limit: int,
    ) -> list[TaiwanInstitutionalDaily]:
        session_factory = self._session_factory()
        with session_factory() as session:
            return list(
                session.execute(
                    select(TaiwanInstitutionalDaily)
                    .where(
                        TaiwanInstitutionalDaily.market == market,
                        TaiwanInstitutionalDaily.symbol == symbol,
                    )
                    .order_by(TaiwanInstitutionalDaily.date.desc())
                    .limit(max(limit, 1))
                ).scalars()
            )


taiwan_institutional_daily_repository = TaiwanInstitutionalDailyRepository()
