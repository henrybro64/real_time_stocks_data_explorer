from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, func

from app.db.models import Base
from sqlalchemy.orm import Mapped, mapped_column


class RealizedTrade(Base):
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

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    buy_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    shares: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    buy_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sell_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
