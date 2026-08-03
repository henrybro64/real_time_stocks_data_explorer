from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stock_id",
            name="uq_watchlist_items_user_stock",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_user_enabled", "user_id", "enabled"),
        Index("ix_alert_rules_enabled_stock", "enabled", "stock_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("true"),
        nullable=False,
    )
    last_condition_met: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        nullable=False,
    )
    last_observed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_observed_quote_timestamp: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        Index("ix_alert_events_user_triggered", "user_id", "triggered_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alert_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    observed_price: Mapped[float] = mapped_column(Float, nullable=False)
    quote_timestamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stock_id",
            name="uq_portfolio_positions_user_stock",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(nullable=False)
    average_cost: Mapped[float] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class PortfolioAsset(Base):
    __tablename__ = "portfolio_assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[float] = mapped_column(nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class PortfolioHistorySnapshot(Base):
    __tablename__ = "portfolio_history_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "snapshot_hour",
            "source_window",
            name="uq_portfolio_history_user_hour_window",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_hour: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    source_window: Mapped[str] = mapped_column(String(8), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    usd_twd_rate: Mapped[float] = mapped_column(nullable=False)
    holdings_value_usd: Mapped[float] = mapped_column(nullable=False)
    holdings_value_twd: Mapped[float] = mapped_column(nullable=False)
    combined_assets_usd: Mapped[float] = mapped_column(nullable=False)
    combined_assets_twd: Mapped[float] = mapped_column(nullable=False)


class TaiwanMarketDailyColumns:
    """Shared identity/audit columns for Taiwan daily market-data tables."""

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date(), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
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


def _taiwan_daily_table_args(unique_name: str, index_name: str) -> tuple:
    return (
        UniqueConstraint(
            "market",
            "symbol",
            "date",
            name=unique_name,
        ),
        Index(
            index_name,
            "market",
            "symbol",
            "date",
        ),
    )


class TaiwanInstitutionalDaily(TaiwanMarketDailyColumns, Base):
    __tablename__ = "taiwan_institutional_daily"
    __table_args__ = _taiwan_daily_table_args(
        "uq_taiwan_institutional_daily_market_symbol_date",
        "ix_taiwan_institutional_daily_lookup",
    )

    foreign_net_shares: Mapped[int] = mapped_column(nullable=False)
    investment_trust_net_shares: Mapped[int] = mapped_column(nullable=False)
    dealer_self_net_shares: Mapped[int | None] = mapped_column(nullable=True)
    dealer_hedge_net_shares: Mapped[int | None] = mapped_column(nullable=True)
    dealer_net_shares: Mapped[int] = mapped_column(nullable=False)
    total_net_shares: Mapped[int] = mapped_column(nullable=False)


class TaiwanMarginShortDaily(TaiwanMarketDailyColumns, Base):
    __tablename__ = "taiwan_margin_short_daily"
    __table_args__ = _taiwan_daily_table_args(
        "uq_taiwan_margin_short_daily_market_symbol_date",
        "ix_taiwan_margin_short_daily_lookup",
    )

    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin_balance_lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_margin_balance_lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrowed_short_balance_lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    borrowed_short_balance_change_lots: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    borrowed_short_balance_change_amount_10k: Mapped[int | None] = mapped_column(
        nullable=True,
    )


class RankingSnapshot(Base):
    __tablename__ = "ranking_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            "capture_scope",
            name="uq_ranking_snapshots_market_board_type_bucket_scope",
        ),
        Index(
            "ix_ranking_snapshots_lookup",
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            "capture_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    tw_board: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ranking_type: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_freshness: Mapped[str] = mapped_column(Text, nullable=False)
    is_market_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    warning_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsupported_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="on_demand",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RankingSnapshotItem(Base):
    __tablename__ = "ranking_snapshot_items"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "rank",
            name="uq_ranking_snapshot_items_snapshot_rank",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ranking_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(nullable=False)
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(nullable=True)
    volume_delta: Mapped[int | None] = mapped_column(nullable=True)
    volume_delta_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_buy: Mapped[int | None] = mapped_column(nullable=True)
    net_sell: Mapped[int | None] = mapped_column(nullable=True)
    data_time: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)


class OAuthIdentity(Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "subject",
            name="uq_oauth_identities_provider_subject",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)


class UserAiPreference(Base):
    __tablename__ = "user_ai_preferences"

    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    preferred_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserApiKey(Base):
    __tablename__ = "user_api_keys"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_user_api_keys_user_provider",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
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
