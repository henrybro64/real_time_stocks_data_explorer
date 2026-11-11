from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.row_mixins import CreatedAtMixin, TimestampedRowMixin, UserOwnedRowMixin


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
    oauth_identities: Mapped[list["OAuthIdentity"]] = relationship(
        back_populates="user",
    )


class WatchlistItem(Base, UserOwnedRowMixin, CreatedAtMixin):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stock_id",
            name="uq_watchlist_items_user_stock",
        ),
    )

    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)


class AlertRule(Base, UserOwnedRowMixin, TimestampedRowMixin):
    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_user_enabled", "user_id", "enabled"),
        Index("ix_alert_rules_enabled_stock", "enabled", "stock_id"),
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


class PortfolioPosition(Base, UserOwnedRowMixin, CreatedAtMixin):
    __tablename__ = "portfolio_positions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stock_id",
            name="uq_portfolio_positions_user_stock",
        ),
    )

    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(nullable=False)
    average_cost: Mapped[float] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PortfolioAsset(Base, UserOwnedRowMixin, TimestampedRowMixin):
    __tablename__ = "portfolio_assets"

    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[float] = mapped_column(nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class TaiwanMarketDailyColumns(TimestampedRowMixin):
    """Shared identity/audit columns for Taiwan daily market-data tables."""

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date(), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)


def _taiwan_daily_table_args(unique_name: str) -> tuple:
    # The unique constraint already covers (market, symbol, date). A second
    # index on the same columns is redundant (#274).
    return (
        UniqueConstraint(
            "market",
            "symbol",
            "date",
            name=unique_name,
        ),
    )


class TaiwanInstitutionalDaily(TaiwanMarketDailyColumns, Base):
    __tablename__ = "taiwan_institutional_daily"
    __table_args__ = _taiwan_daily_table_args(
        "uq_taiwan_institutional_daily_market_symbol_date",
    )

    foreign_net_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    investment_trust_net_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dealer_self_net_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dealer_hedge_net_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dealer_net_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_net_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TaiwanMarginShortDaily(TaiwanMarketDailyColumns, Base):
    __tablename__ = "taiwan_margin_short_daily"
    __table_args__ = _taiwan_daily_table_args(
        "uq_taiwan_margin_short_daily_market_symbol_date",
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


class DailyPriceBar(TimestampedRowMixin, Base):
    """Persisted OHLCV daily bars shared by charts, key stats, and analysis (#269)."""

    __tablename__ = "daily_price_bars"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "symbol",
            "date",
            name="uq_daily_price_bars_market_symbol_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date(), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class DailyPriceMonthFetch(Base):
    """A month of daily history that has already been requested from upstream (#269)."""

    __tablename__ = "daily_price_month_fetches"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "symbol",
            "month_start",
            name="uq_daily_price_month_fetches_market_symbol_month",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    month_start: Mapped[date] = mapped_column(Date(), nullable=False)
    bar_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReferenceSnapshot(Base):
    """Last good catalog or holiday calendar, so a restart keeps it (#264)."""

    __tablename__ = "reference_snapshots"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
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
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    tw_board: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume_delta: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume_delta_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_buy: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    net_sell: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    user: Mapped["User"] = relationship(back_populates="oauth_identities")


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


class UserApiKey(Base, UserOwnedRowMixin, TimestampedRowMixin):
    __tablename__ = "user_api_keys"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_user_api_keys_user_provider",
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)


class TwResearchScan(Base):
    """Durable Taiwan research scan metadata (#318)."""

    __tablename__ = "tw_research_scans"
    __table_args__ = (
        Index(
            "ix_tw_research_scans_universe_trading_day",
            "universe_version",
            "trading_day",
            "status",
        ),
    )

    scan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trading_day: Mapped[str] = mapped_column(String(10), nullable=False)
    universe_version: Mapped[str] = mapped_column(String(128), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    enrichment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selector_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    stock_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    cursor: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    # Exclusive end of the in-flight batch reservation; cursor stays at the
    # completed watermark until the batch finishes or the lease expires (#318).
    reserve_cursor: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    reserve_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    enrich_cursor: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    # In-flight enrichment reservation (stock ids) + lease expiry (#318).
    enrich_reserve_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    enrich_reserve_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Bumped on every enrichment claim so stale workers cannot write after reclaim.
    enrich_lease_generation: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0")
    )
    # Bumped on every scan-batch claim so expired workers cannot complete/corrupt (#329).
    scan_lease_generation: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0")
    )
    # Explicit #329 resume windows granted (bounded by max_upstream_month_fetches cap).
    budget_windows_opened: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0")
    )
    target_count: Mapped[int] = mapped_column(nullable=False)
    upstream_month_fetches: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    max_upstream_month_fetches: Mapped[int] = mapped_column(nullable=False)
    enrichments_run: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    max_enrichments_per_run: Mapped[int] = mapped_column(nullable=False)
    day_pass_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    qualified_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    notes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    active_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


class TwResearchSymbolResult(Base):
    """Per-symbol checkpoint + ledger payload for a research scan (#318)."""

    __tablename__ = "tw_research_symbol_results"
    __table_args__ = (
        UniqueConstraint(
            "scan_id",
            "stock_id",
            name="uq_tw_research_symbol_results_scan_stock",
        ),
        UniqueConstraint(
            "trading_day",
            "universe_version",
            "calculation_version",
            "stock_id",
            "scan_id",
            name="uq_tw_research_checkpoint_identity",
        ),
        Index("ix_tw_research_symbol_results_scan", "scan_id", "stock_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tw_research_scans.scan_id", ondelete="CASCADE"),
        nullable=False,
    )
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), nullable=False)
    universe_version: Mapped[str] = mapped_column(String(128), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    readiness: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TwResearchHistoryFlight(Base):
    """Cross-pod single-flight lease for compatible OHLCV backfills (#329)."""

    __tablename__ = "tw_research_history_flights"
    __table_args__ = (
        Index("ix_tw_research_history_flights_stock", "stock_id", "state"),
    )

    flight_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    compatibility_key: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_token: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
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


class TwResearchCoordinationLock(Base):
    """Named PostgreSQL row locks for cluster-wide admission / schedule ticks (#329/#330)."""

    __tablename__ = "tw_research_coordination_locks"

    lock_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    holder_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TwResearchMembership(Base):
    """User-scoped editable research membership + compute subset (#336)."""

    __tablename__ = "tw_research_memberships"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    base_preset_version: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'starter'")
    )
    theme_keys_json: Mapped[list] = mapped_column(JSON, nullable=False)
    disabled_theme_keys_json: Mapped[list] = mapped_column(JSON, nullable=False)
    sector_additions_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    sector_removals_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    custom_sectors_json: Mapped[list] = mapped_column(JSON, nullable=False)
    extra_stock_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    compute_subset_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TwResearchFirstPassJob(Base):
    """Resumable full-universe first-pass collection job (#335)."""

    __tablename__ = "tw_research_first_pass_jobs"
    __table_args__ = (
        Index(
            "ix_tw_research_first_pass_jobs_user_updated",
            "user_id",
            "updated_at",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_version: Mapped[str] = mapped_column(String(128), nullable=False)
    universe_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    membership_version: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    first_pass_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    selector_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    # pending|running|completed|partial|failed|deferred
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # data completeness (independent of processing status)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    cursor: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    reserve_cursor: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    reserve_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lease_generation: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0")
    )
    active_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes_json: Mapped[list] = mapped_column(JSON, nullable=False)
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


class TwResearchFirstPassSymbolResult(Base):
    """Per-symbol first-pass evidence checkpoint for a job (#335)."""

    __tablename__ = "tw_research_first_pass_symbol_results"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "stock_id",
            name="uq_tw_research_first_pass_symbol_job_stock",
        ),
        Index("ix_tw_research_first_pass_symbol_job", "job_id", "stock_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tw_research_first_pass_jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )
    stock_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # pending|ok|partial|failed|deferred
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    lease_generation: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TwResearchFirstPassSnapshot(Base):
    """Immutable first-pass evidence batch for analyst handoff (#335/#336)."""

    __tablename__ = "tw_research_first_pass_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "content_digest",
            name="uq_tw_research_first_pass_user_digest",
        ),
        Index(
            "ix_tw_research_first_pass_user_created",
            "user_id",
            "created_at",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_version: Mapped[str] = mapped_column(String(128), nullable=False)
    first_pass_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    stock_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class TwResearchCandidateSet(Base):
    """Analyst-selected technical compute subset pinned to first-pass digest (#336)."""

    __tablename__ = "tw_research_candidate_sets"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "version",
            name="uq_tw_research_candidate_sets_user_version",
        ),
        Index(
            "ix_tw_research_candidate_sets_user_created",
            "user_id",
            "created_at",
        ),
    )

    candidate_set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    first_pass_snapshot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tw_research_first_pass_snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    first_pass_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_reasons_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    decision_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    notes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class TwResearchExportSnapshot(Base):
    """Immutable finalized export snapshot (#318/#319)."""

    __tablename__ = "tw_research_export_snapshots"
    __table_args__ = (
        # Same research content → same digest; uniqueness is per owner so users
        # never share snapshot rows across authorization boundaries (#319).
        UniqueConstraint(
            "user_id",
            "content_digest",
            name="uq_tw_research_export_user_content_digest",
        ),
        Index(
            "ix_tw_research_export_latest",
            "universe_version",
            "trading_day",
            "quality_status",
            "finalized_at",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # SET NULL on scan delete so verified snapshots outlive scan retention.
    scan_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("tw_research_scans.scan_id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    universe_version: Mapped[str] = mapped_column(String(128), nullable=False)
    trading_day: Mapped[str] = mapped_column(String(10), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    enrichment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    ledger_json: Mapped[list] = mapped_column(JSON, nullable=False)
    chunk_digests_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
