from __future__ import annotations

from datetime import date
from typing import Any, Generic, TypeVar

from app.db.models import TaiwanInstitutionalDaily, TaiwanMarginShortDaily
from app.services.tw_institutional_service import TaiwanInstitutionalInvestorRecord
from app.services.tw_margin_short_service import TaiwanMarginShortRecord

from .session import (
    DailyUpsertStats,
    SessionBoundRepository,
    list_market_symbol_history,
    upsert_market_symbol_date_rows,
)

ModelT = TypeVar("ModelT")
RecordT = TypeVar("RecordT")
InstitutionalDailyUpsertStats = DailyUpsertStats
MarginShortDailyUpsertStats = DailyUpsertStats

__all__ = [
    "DailyUpsertStats",
    "InstitutionalDailyUpsertStats",
    "MarginShortDailyUpsertStats",
    "TaiwanInstitutionalDailyRepository",
    "TaiwanMarginShortDailyRepository",
    "taiwan_institutional_daily_repository",
    "taiwan_margin_short_daily_repository",
]


def _copy_record_fields(
    existing: Any,
    record: Any,
    *,
    source: str,
    fields: tuple[str, ...],
    nullable_name: bool = True,
    preserve_existing_when_null: frozenset[str] | None = None,
) -> None:
    preserve = preserve_existing_when_null or frozenset()
    for field in fields:
        value = getattr(record, field)
        if field == "name" and nullable_name:
            value = value or None
        if field in preserve and value is None:
            # Keep previously stored values when a partial refresh omits a source.
            continue
        setattr(existing, field, value)
    existing.source = source


class _TaiwanDailyRepository(SessionBoundRepository, Generic[ModelT, RecordT]):
    model: type[ModelT]

    def list_history(
        self,
        *,
        market: str,
        symbol: str,
        limit: int,
    ) -> list[ModelT]:
        return list_market_symbol_history(
            self._session_factory(),
            model=self.model,
            market=market,
            symbol=symbol,
            limit=limit,
        )

    def _upsert(
        self,
        *,
        market: str,
        source: str,
        records: list[RecordT],
        build_row,
        fields: tuple[str, ...],
        preserve_existing_when_null: frozenset[str] | None = None,
    ) -> DailyUpsertStats:
        def apply_update(existing: ModelT, record: RecordT, row_source: str) -> None:
            _copy_record_fields(
                existing,
                record,
                source=row_source,
                fields=fields,
                preserve_existing_when_null=preserve_existing_when_null,
            )

        return upsert_market_symbol_date_rows(
            self._session_factory(),
            model=self.model,
            market=market,
            source=source,
            records=records,
            build_row=build_row,
            apply_update=apply_update,
        )


class TaiwanInstitutionalDailyRepository(
    _TaiwanDailyRepository[TaiwanInstitutionalDaily, TaiwanInstitutionalInvestorRecord]
):
    model = TaiwanInstitutionalDaily
    _FIELDS = (
        "name",
        "foreign_net_shares",
        "investment_trust_net_shares",
        "dealer_self_net_shares",
        "dealer_hedge_net_shares",
        "dealer_net_shares",
        "total_net_shares",
    )

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

        return self._upsert(
            market=market,
            source=source,
            records=records,
            build_row=build_row,
            fields=self._FIELDS,
        )


class TaiwanMarginShortDailyRepository(
    _TaiwanDailyRepository[TaiwanMarginShortDaily, TaiwanMarginShortRecord]
):
    model = TaiwanMarginShortDaily
    _FIELDS = (
        "name",
        "close_price",
        "margin_balance_lots",
        "short_margin_balance_lots",
        "borrowed_short_balance_lots",
        "borrowed_short_balance_change_lots",
        "borrowed_short_balance_change_amount_10k",
    )
    # Partial day merges emit nulls for the missing core source; never overwrite
    # previously populated balances/close with those absent-source nulls.
    _PRESERVE_WHEN_NULL = frozenset(_FIELDS)

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

        return self._upsert(
            market=market,
            source=source,
            records=records,
            build_row=build_row,
            fields=self._FIELDS,
            preserve_existing_when_null=self._PRESERVE_WHEN_NULL,
        )


taiwan_institutional_daily_repository = TaiwanInstitutionalDailyRepository()
taiwan_margin_short_daily_repository = TaiwanMarginShortDailyRepository()
