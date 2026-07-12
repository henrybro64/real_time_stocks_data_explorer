from __future__ import annotations

from datetime import date

from app.db.models import TaiwanInstitutionalDaily
from app.services.tw_institutional_service import TaiwanInstitutionalInvestorRecord

from .session import (
    DailyUpsertStats,
    SessionBoundRepository,
    list_market_symbol_history,
    upsert_market_symbol_date_rows,
)

# Re-export for callers/tests that imported the old name.
InstitutionalDailyUpsertStats = DailyUpsertStats


class TaiwanInstitutionalDailyRepository(SessionBoundRepository):
    def upsert_records(
        self,
        *,
        market: str,
        source: str,
        records: list[TaiwanInstitutionalInvestorRecord],
    ) -> DailyUpsertStats:
        def build_row(record: TaiwanInstitutionalInvestorRecord, trade_date: date):
            return TaiwanInstitutionalDaily(
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

        def apply_update(
            existing: TaiwanInstitutionalDaily,
            record: TaiwanInstitutionalInvestorRecord,
            row_source: str,
        ) -> None:
            existing.name = record.name or None
            existing.foreign_net_shares = record.foreign_net_shares
            existing.investment_trust_net_shares = record.investment_trust_net_shares
            existing.dealer_self_net_shares = record.dealer_self_net_shares
            existing.dealer_hedge_net_shares = record.dealer_hedge_net_shares
            existing.dealer_net_shares = record.dealer_net_shares
            existing.total_net_shares = record.total_net_shares
            existing.source = row_source

        return upsert_market_symbol_date_rows(
            self._session_factory(),
            model=TaiwanInstitutionalDaily,
            market=market,
            source=source,
            records=records,
            build_row=build_row,
            apply_update=apply_update,
        )

    def list_history(
        self,
        *,
        market: str,
        symbol: str,
        limit: int,
    ) -> list[TaiwanInstitutionalDaily]:
        return list_market_symbol_history(
            self._session_factory(),
            model=TaiwanInstitutionalDaily,
            market=market,
            symbol=symbol,
            limit=limit,
        )


taiwan_institutional_daily_repository = TaiwanInstitutionalDailyRepository()
