"""Postgres access for persisted daily OHLCV bars (#269)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import DailyPriceBar, DailyPriceMonthFetch
from app.models.quote import CandleRecord

from .session import SessionBoundRepository


@dataclass(frozen=True)
class DailyBarUpsertStats:
    inserted: int = 0
    updated: int = 0


class DailyPriceBarRepository(SessionBoundRepository):
    def list_bars(
        self,
        *,
        market: str,
        symbol: str,
        limit: int,
    ) -> list[DailyPriceBar]:
        if limit <= 0:
            return []
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(DailyPriceBar)
                .where(
                    DailyPriceBar.market == market,
                    DailyPriceBar.symbol == symbol,
                )
                .order_by(DailyPriceBar.date.desc())
                .limit(limit)
            ).scalars()
            return list(reversed(list(rows)))

    def latest_date(self, *, market: str, symbol: str) -> date | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            return session.execute(
                select(DailyPriceBar.date)
                .where(
                    DailyPriceBar.market == market,
                    DailyPriceBar.symbol == symbol,
                )
                .order_by(DailyPriceBar.date.desc())
                .limit(1)
            ).scalar_one_or_none()

    def upsert_candles(
        self,
        *,
        market: str,
        symbol: str,
        candles: list[CandleRecord],
        source: str,
    ) -> DailyBarUpsertStats:
        if not candles:
            return DailyBarUpsertStats()
        session_factory = self._session_factory()
        inserted = 0
        updated = 0
        with session_factory() as session:
            for candle in candles:
                try:
                    bar_date = date.fromisoformat(candle.time[:10])
                except ValueError:
                    continue
                row = DailyPriceBar(
                    market=market,
                    symbol=symbol,
                    date=bar_date,
                    open=float(candle.open),
                    high=float(candle.high),
                    low=float(candle.low),
                    close=float(candle.close),
                    volume=int(candle.volume),
                    source=source,
                )
                try:
                    with session.begin_nested():
                        session.add(row)
                        session.flush()
                    inserted += 1
                except IntegrityError:
                    existing = session.execute(
                        select(DailyPriceBar).where(
                            DailyPriceBar.market == market,
                            DailyPriceBar.symbol == symbol,
                            DailyPriceBar.date == bar_date,
                        )
                    ).scalar_one()
                    existing.open = float(candle.open)
                    existing.high = float(candle.high)
                    existing.low = float(candle.low)
                    existing.close = float(candle.close)
                    existing.volume = int(candle.volume)
                    existing.source = source
                    updated += 1
            session.commit()
        return DailyBarUpsertStats(inserted=inserted, updated=updated)

    def list_fetched_months(self, *, market: str, symbol: str) -> set[str]:
        """Month keys ``YYYY-MM`` already requested, including empty official months."""
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(DailyPriceMonthFetch.month_start).where(
                    DailyPriceMonthFetch.market == market,
                    DailyPriceMonthFetch.symbol == symbol,
                )
            ).scalars()
            return {row.strftime("%Y-%m") for row in rows}

    def mark_month_fetched(
        self,
        *,
        market: str,
        symbol: str,
        month_start: date,
        bar_count: int,
    ) -> None:
        session_factory = self._session_factory()
        with session_factory() as session:
            existing = session.execute(
                select(DailyPriceMonthFetch).where(
                    DailyPriceMonthFetch.market == market,
                    DailyPriceMonthFetch.symbol == symbol,
                    DailyPriceMonthFetch.month_start == month_start,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    DailyPriceMonthFetch(
                        market=market,
                        symbol=symbol,
                        month_start=month_start,
                        bar_count=bar_count,
                    )
                )
            else:
                existing.bar_count = bar_count
            session.commit()


def bars_to_candles(rows: list[DailyPriceBar]) -> list[CandleRecord]:
    return [
        CandleRecord(
            time=row.date.isoformat(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=int(row.volume),
        )
        for row in rows
    ]


daily_price_bar_repository = DailyPriceBarRepository()
