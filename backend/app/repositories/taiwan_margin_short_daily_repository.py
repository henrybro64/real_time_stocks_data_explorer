from __future__ import annotations

from datetime import date

from app.db.models import TaiwanMarginShortDaily
from app.services.tw_margin_short_service import TaiwanMarginShortRecord

from .session import (
    DailyUpsertStats,
    SessionBoundRepository,
    list_market_symbol_history,
    upsert_market_symbol_date_rows,
)

# Re-export for callers/tests that imported the old name.
MarginShortDailyUpsertStats = DailyUpsertStats


class TaiwanMarginShortDailyRepository(SessionBoundRepository):
    def upsert_records(
        self,
        *,
        market: str,
        source: str,
        records: list[TaiwanMarginShortRecord],
    ) -> DailyUpsertStats:
        def build_row(record: TaiwanMarginShortRecord, trade_date: date):
            return TaiwanMarginShortDaily(
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

        def apply_update(
            existing: TaiwanMarginShortDaily,
            record: TaiwanMarginShortRecord,
            row_source: str,
        ) -> None:
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
            existing.source = row_source

        return upsert_market_symbol_date_rows(
            self._session_factory(),
            model=TaiwanMarginShortDaily,
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
    ) -> list[TaiwanMarginShortDaily]:
        return list_market_symbol_history(
            self._session_factory(),
            model=TaiwanMarginShortDaily,
            market=market,
            symbol=symbol,
            limit=limit,
        )


taiwan_margin_short_daily_repository = TaiwanMarginShortDailyRepository()
