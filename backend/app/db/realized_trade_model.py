from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.row_mixins import TimestampedRowMixin, UserOwnedRowMixin


class RealizedTrade(Base, UserOwnedRowMixin, TimestampedRowMixin):
    __tablename__ = "realized_trades"
    __table_args__ = (
        Index("ix_realized_trades_user_sell_date", "user_id", "sell_date"),
        CheckConstraint("buy_price > 0", name="ck_realized_trades_buy_price_positive"),
        CheckConstraint("sell_price > 0", name="ck_realized_trades_sell_price_positive"),
        CheckConstraint("shares > 0", name="ck_realized_trades_shares_positive"),
        CheckConstraint(
            "currency IN ('TWD', 'USD')",
            name="ck_realized_trades_currency",
        ),
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    buy_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    shares: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    buy_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sell_date: Mapped[date] = mapped_column(Date, nullable=False)
