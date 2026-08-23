"""Idempotent repository for public ranking archives in Azure SQL (or sqlite tests)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.archive_models import RankingSnapshotArchive, RankingSnapshotItemArchive
from app.db.archive_session import (
    ensure_rankings_archive_schema,
    get_rankings_archive_session_factory,
    rankings_archive_is_configured,
)
from app.schemas.rankings import RankingItem, RankingResponse, RankingType

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
                last_price=item.lastPrice,
                change_percent=item.changePercent,
                volume=item.volume,
                volume_delta_percent=item.volumeDeltaPercent,
                pe_ratio=item.peRatio,
                pe_ratio_type=item.peRatioType,
                pe_ratio_source=item.peRatioSource,
                valuation_as_of=item.valuationAsOf,
                net_buy_sell_shares=item.netBuySellShares,
                data_time=item.timestamp,
            )
        )
    return rows


rankings_archive_repository = RankingsArchiveRepository()
