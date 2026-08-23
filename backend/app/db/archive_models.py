"""Azure SQL / optional archive ORM models (public ranking data only).

These tables are intentionally separate from the primary PostgreSQL schema and
must never store personal/user data.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class ArchiveBase(DeclarativeBase):
    """Declarative base for the optional rankings archive database."""


class RankingSnapshotArchive(ArchiveBase):
    __tablename__ = "ranking_snapshots_archive"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "universe",
            "ranking_type",
            "trading_date",
            name="uq_ranking_snapshots_archive_market_universe_type_date",
        ),
        Index(
            "ix_ranking_snapshots_archive_lookup",
            "market",
            "universe",
            "ranking_type",
            "trading_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    universe: Mapped[str] = mapped_column(String(16), nullable=False)
    ranking_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_freshness: Mapped[str] = mapped_column(Text, nullable=False)
    is_market_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    warning_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsupported_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="end_of_day",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    items: Mapped[list["RankingSnapshotItemArchive"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="RankingSnapshotItemArchive.rank",
    )


class RankingSnapshotItemArchive(ArchiveBase):
    __tablename__ = "ranking_snapshot_items_archive"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "rank",
            name="uq_ranking_snapshot_items_archive_snapshot_rank",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ranking_snapshots_archive.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_delta_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ratio_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pe_ratio_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    valuation_as_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    net_buy_sell_shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_time: Mapped[str] = mapped_column(String(64), nullable=False)

    snapshot: Mapped[RankingSnapshotArchive] = relationship(back_populates="items")
