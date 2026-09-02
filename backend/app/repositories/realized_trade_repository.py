from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select

from app.db.models import RealizedTrade

from .session import require_session_factory


def _owned_trade_query(user_id: str, trade_id: int):
    return select(RealizedTrade).where(
        RealizedTrade.id == trade_id,
        RealizedTrade.user_id == user_id,
    )


class RealizedTradeRepository:
    def list_trades(
        self,
        user_id: str,
        *,
        currency: str | None = None,
    ) -> list[RealizedTrade]:
        session_factory = require_session_factory()
        with session_factory() as session:
            query = (
                select(RealizedTrade)
                .where(RealizedTrade.user_id == user_id)
                .order_by(RealizedTrade.sell_date.desc(), RealizedTrade.id.desc())
            )
            if currency is not None:
                query = query.where(RealizedTrade.currency == currency)
            return list(session.execute(query).scalars())

    def get_trade(self, user_id: str, trade_id: int) -> RealizedTrade | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            return session.execute(_owned_trade_query(user_id, trade_id)).scalar_one_or_none()

    def create_trade(
        self,
        *,
        user_id: str,
        symbol: str,
        currency: str,
        buy_price: Decimal,
        sell_price: Decimal,
        shares: Decimal,
        buy_date: date | None,
        sell_date: date,
    ) -> RealizedTrade:
        session_factory = require_session_factory()
        with session_factory() as session:
            trade = RealizedTrade(
                user_id=user_id,
                symbol=symbol,
                currency=currency,
                buy_price=buy_price,
                sell_price=sell_price,
                shares=shares,
                buy_date=buy_date,
                sell_date=sell_date,
            )
            session.add(trade)
            session.commit()
            session.refresh(trade)
            return trade

    def update_trade(
        self,
        user_id: str,
        trade_id: int,
        *,
        symbol: str,
        currency: str,
        buy_price: Decimal,
        sell_price: Decimal,
        shares: Decimal,
        buy_date: date | None,
        sell_date: date,
    ) -> RealizedTrade | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            trade = session.execute(_owned_trade_query(user_id, trade_id)).scalar_one_or_none()
            if trade is None:
                return None
            trade.symbol = symbol
            trade.currency = currency
            trade.buy_price = buy_price
            trade.sell_price = sell_price
            trade.shares = shares
            trade.buy_date = buy_date
            trade.sell_date = sell_date
            session.commit()
            session.refresh(trade)
            return trade

    def delete_trade(self, user_id: str, trade_id: int) -> bool:
        session_factory = require_session_factory()
        with session_factory() as session:
            result = session.execute(
                delete(RealizedTrade).where(
                    RealizedTrade.id == trade_id,
                    RealizedTrade.user_id == user_id,
                ),
            )
            session.commit()
            return (result.rowcount or 0) > 0


realized_trade_repository = RealizedTradeRepository()
