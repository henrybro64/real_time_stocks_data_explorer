"""Idempotent repository for public ranking archives in Azure SQL (or sqlite tests)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging

from sqlalchemy import distinct, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.archive_models import RankingSnapshotArchive, RankingSnapshotItemArchive
from app.db.archive_session import (
    ensure_rankings_archive_schema,
    get_rankings_archive_session_factory,
    rankings_archive_is_configured,
)
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
    TwBoard,
)
from app.schemas.stock import StockResponse

logger = logging.getLogger(__name__)

CAPTURE_SCOPE_END_OF_DAY = "end_of_day"
_UNSET = object()


@dataclass(frozen=True)
class ArchiveSnapshotEnvelope:
    snapshot: RankingSnapshotArchive
    items: list[RankingSnapshotItemArchive]


class RankingsArchiveRepository:
    def __init__(self) -> None:
        self._session_factory_override: sessionmaker[Session] | None | object = _UNSET

    def set_session_factory_override(self, session_factory: sessionmaker[Session] | None) -> None:
        self._session_factory_override = session_factory

    def is_available(self) -> bool:
        if self._session_factory_override is not _UNSET:
            return self._session_factory_override is not None
        return (
            rankings_archive_is_configured()
            and get_rankings_archive_session_factory() is not None
        )

    def is_configured(self) -> bool:
        if self._session_factory_override is not _UNSET:
            return self._session_factory_override is not None
        return rankings_archive_is_configured()

    def _session_factory(self) -> sessionmaker[Session]:
        if self._session_factory_override is not _UNSET:
            if self._session_factory_override is None:
                raise RuntimeError("Rankings archive database is not configured")
            return self._session_factory_override
        factory = get_rankings_archive_session_factory()
        if factory is None:
            raise RuntimeError("Rankings archive database is not configured")
        return factory

    def ensure_schema(self) -> bool:
        if self._session_factory_override is not _UNSET:
            if self._session_factory_override is None:
                return False
            with self._session_factory_override() as session:
                bind = session.get_bind()
                from app.db.archive_models import ArchiveBase

                ArchiveBase.metadata.create_all(bind=bind)
            return True
        return ensure_rankings_archive_schema()

    def list_available_trading_dates(
        self,
        *,
        market: str,
        universe: str,
        ranking_type: RankingType | str | None = None,
        limit: int = 5,
    ) -> list[date]:
        """Return archived trading dates for market/universe, newest first.

        When ``ranking_type`` is omitted, dates are distinct across all archived
        ranking types for that market/universe (UI date discovery use case).
        """
        if not self.is_available() or limit <= 0:
            return []
        session_factory = self._session_factory()
        with session_factory() as session:
            stmt = select(distinct(RankingSnapshotArchive.trading_date)).where(
                RankingSnapshotArchive.market == market,
                RankingSnapshotArchive.universe == universe,
            )
            if ranking_type is not None:
                ranking_type_value = (
                    ranking_type.value
                    if isinstance(ranking_type, RankingType)
                    else str(ranking_type)
                )
                stmt = stmt.where(
                    RankingSnapshotArchive.ranking_type == ranking_type_value,
                )
            stmt = stmt.order_by(RankingSnapshotArchive.trading_date.desc()).limit(limit)
            return list(session.execute(stmt).scalars())

    def load_eod_snapshot(
        self,
        *,
        market: str,
        universe: str,
        ranking_type: RankingType | str,
        trading_date: date,
    ) -> ArchiveSnapshotEnvelope | None:
        if not self.is_available():
            return None
        ranking_type_value = (
            ranking_type.value if isinstance(ranking_type, RankingType) else str(ranking_type)
        )
        session_factory = self._session_factory()
        with session_factory() as session:
            snapshot = session.execute(
                select(RankingSnapshotArchive).where(
                    RankingSnapshotArchive.market == market,
                    RankingSnapshotArchive.universe == universe,
                    RankingSnapshotArchive.ranking_type == ranking_type_value,
                    RankingSnapshotArchive.trading_date == trading_date,
                )
            ).scalar_one_or_none()
            if snapshot is None:
                return None
            items = list(
                session.execute(
                    select(RankingSnapshotItemArchive)
                    .where(RankingSnapshotItemArchive.snapshot_id == snapshot.id)
                    .order_by(RankingSnapshotItemArchive.rank.asc())
                ).scalars()
            )
            return ArchiveSnapshotEnvelope(snapshot=snapshot, items=items)

    def upsert_eod_snapshot(
        self,
        *,
        market: str,
        universe: str,
        ranking_type: RankingType | str,
        trading_date: date,
        response: RankingResponse,
        captured_at: datetime | None = None,
        capture_scope: str = CAPTURE_SCOPE_END_OF_DAY,
    ) -> ArchiveSnapshotEnvelope | None:
        """Write an EOD archive row. Duplicate keys keep the existing snapshot."""
        if not self.is_available():
            return None
        if not self.ensure_schema():
            return None
        ranking_type_value = (
            ranking_type.value if isinstance(ranking_type, RankingType) else str(ranking_type)
        )
        captured = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        session_factory = self._session_factory()
        with session_factory() as session:
            existing = session.execute(
                select(RankingSnapshotArchive).where(
                    RankingSnapshotArchive.market == market,
                    RankingSnapshotArchive.universe == universe,
                    RankingSnapshotArchive.ranking_type == ranking_type_value,
                    RankingSnapshotArchive.trading_date == trading_date,
                )
            ).scalar_one_or_none()
            if existing is not None:
                items = list(
                    session.execute(
                        select(RankingSnapshotItemArchive)
                        .where(RankingSnapshotItemArchive.snapshot_id == existing.id)
                        .order_by(RankingSnapshotItemArchive.rank.asc())
                    ).scalars()
                )
                return ArchiveSnapshotEnvelope(snapshot=existing, items=items)

            snapshot = RankingSnapshotArchive(
                market=market,
                universe=universe,
                ranking_type=ranking_type_value,
                trading_date=trading_date,
                captured_at=captured,
                source=response.source,
                source_type=response.sourceType.value,
                source_freshness=response.dataFreshnessLabel,
                is_market_open=response.isMarketOpen,
                warning_message=response.warningMessage,
                unsupported_reason=response.unsupportedReason,
                capture_scope=capture_scope,
                tw_rankings_scope=response.twRankingsScope,
            )
            session.add(snapshot)
            try:
                session.flush()
                session.add_all(_items_from_response(snapshot.id, response))
                session.commit()
            except IntegrityError:
                session.rollback()
                return self.load_eod_snapshot(
                    market=market,
                    universe=universe,
                    ranking_type=ranking_type_value,
                    trading_date=trading_date,
                )

        return self.load_eod_snapshot(
            market=market,
            universe=universe,
            ranking_type=ranking_type_value,
            trading_date=trading_date,
        )

    def to_ranking_response(self, envelope: ArchiveSnapshotEnvelope) -> RankingResponse:
        """Rebuild a RankingResponse from an archived EOD snapshot (rank order preserved)."""
        return ranking_response_from_archive(envelope)


def ranking_response_from_archive(envelope: ArchiveSnapshotEnvelope) -> RankingResponse:
    """Map archive ORM rows back to the API RankingResponse shape."""
    snapshot = envelope.snapshot
    market = snapshot.market
    universe = snapshot.universe
    tw_board: TwBoard | None = None
    us_scope: str | None = None
    tw_scope: str | None = snapshot.tw_rankings_scope
    if market == "TW" and universe in {"TWSE", "TPEx"}:
        tw_board = universe  # type: ignore[assignment]
        if tw_scope is None and RankingType(snapshot.ranking_type) not in {
            RankingType.net_buy,
            RankingType.net_sell,
        }:
            # Legacy rows captured before tw_rankings_scope was persisted.
            tw_scope = "quote"
    elif market == "US" and universe in {"DJIA", "NASDAQ100"}:
        us_scope = "djia" if universe == "DJIA" else "nasdaq100"
        tw_scope = None

    items = [
        RankingItem(
            rank=row.rank,
            stock=_stock_from_archive_item(row, market=market, universe=universe),
            lastPrice=row.last_price,
            changePercent=row.change_percent,
            volume=row.volume,
            volumeDeltaPercent=row.volume_delta_percent,
            netBuySellShares=row.net_buy_sell_shares,
            peRatio=row.pe_ratio,
            peRatioType=row.pe_ratio_type,
            peRatioSource=row.pe_ratio_source,
            valuationAsOf=row.valuation_as_of,
            valuationWarning=row.valuation_warning,
            timestamp=row.data_time,
        )
        for row in envelope.items
    ]
    return RankingResponse(
        market=market,
        rankingType=RankingType(snapshot.ranking_type),
        twBoard=tw_board,
        universe=universe if universe in {"TWSE", "TPEx", "DJIA", "NASDAQ100"} else None,  # type: ignore[arg-type]
        items=items,
        timestamp=_as_utc_isoformat(snapshot.captured_at),
        source=snapshot.source,
        sourceType=RankingSourceType(snapshot.source_type),
        isMarketOpen=False,
        dataFreshnessLabel=snapshot.source_freshness,
        warningMessage=snapshot.warning_message,
        unsupportedReason=snapshot.unsupported_reason,
        usRankingsScope=us_scope,
        twRankingsScope=tw_scope,
        tradingDate=snapshot.trading_date.isoformat(),
        captureScope=snapshot.capture_scope,
    )


def _as_utc_isoformat(value: datetime) -> str:
    """Serialize captured_at as UTC ISO-8601.

    SQLite often returns naive datetimes for ``DateTime(timezone=True)`` columns.
    Treat naive values as UTC (how we write them) instead of interpreting them in
    the process local timezone.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.astimezone(timezone.utc).isoformat()


def _stock_from_archive_item(
    item: RankingSnapshotItemArchive,
    *,
    market: str,
    universe: str,
) -> StockResponse:
    if market == "TW":
        currency = "TWD"
        timezone_name = "Asia/Taipei"
        fallback_exchange = universe if universe in {"TWSE", "TPEx"} else "TWSE"
    else:
        currency = "USD"
        timezone_name = "America/New_York"
        fallback_exchange = "NASDAQ"
    # getattr: tolerate older archive rows reconstructed without an exchange column.
    exchange = (getattr(item, "exchange", None) or "").strip() or fallback_exchange
    return StockResponse(
        id=item.stock_id,
        market=market,
        symbol=item.symbol,
        name=item.name,
        exchange=exchange,
        currency=currency,
        timezone=timezone_name,
    )


def _items_from_response(
    snapshot_id: int,
    response: RankingResponse,
) -> list[RankingSnapshotItemArchive]:
    rows: list[RankingSnapshotItemArchive] = []
    for item in response.items:
        rows.append(
            RankingSnapshotItemArchive(
                snapshot_id=snapshot_id,
                rank=item.rank,
                stock_id=item.stock.id,
                symbol=item.stock.symbol,
                name=item.stock.name,
                exchange=item.stock.exchange,
                last_price=item.lastPrice,
                change_percent=item.changePercent,
                volume=item.volume,
                volume_delta_percent=item.volumeDeltaPercent,
                pe_ratio=item.peRatio,
                pe_ratio_type=item.peRatioType,
                pe_ratio_source=item.peRatioSource,
                valuation_as_of=item.valuationAsOf,
                valuation_warning=item.valuationWarning,
                net_buy_sell_shares=item.netBuySellShares,
                data_time=item.timestamp,
            )
        )
    return rows


rankings_archive_repository = RankingsArchiveRepository()
