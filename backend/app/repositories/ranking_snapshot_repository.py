from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import RankingSnapshot, RankingSnapshotItem
from app.schemas.rankings import RankingItem, RankingResponse, RankingType
from app.services.ranking_data_providers import RankingQuery

from .session import database_is_configured, require_session_factory

_UNSET = object()


@dataclass(frozen=True)
class SnapshotEnvelope:
    snapshot: RankingSnapshot
    items: list[RankingSnapshotItem]


class RankingSnapshotRepository:
    def __init__(self) -> None:
        self._session_factory_override = _UNSET

    def set_session_factory_override(self, session_factory) -> None:
        self._session_factory_override = session_factory

    def is_available(self) -> bool:
        if self._session_factory_override is not _UNSET:
            return self._session_factory_override is not None
        return database_is_configured()

    def _session_factory(self):
        if self._session_factory_override is not _UNSET:
            if self._session_factory_override is None:
                raise RuntimeError("DATABASE_URL is not configured")
            return self._session_factory_override
        return require_session_factory()

    def persist_snapshot(
        self,
        query: RankingQuery,
        bucket_start: datetime,
        response: RankingResponse,
        *,
        capture_scope: str,
    ) -> SnapshotEnvelope:
        session_factory = self._session_factory()
        with session_factory() as session:
            snapshot = RankingSnapshot(
                market=query.market,
                tw_board=self._db_tw_board(query),
                ranking_type=query.ranking_type.value,
                bucket_start=bucket_start,
                source=response.source,
                source_type=response.sourceType.value,
                source_freshness=response.dataFreshnessLabel,
                is_market_open=response.isMarketOpen,
                warning_message=response.warningMessage,
                unsupported_reason=response.unsupportedReason,
                capture_scope=capture_scope,
            )
            try:
                with session.begin_nested():
                    session.add(snapshot)
                    session.flush()
                    session.add_all(
                        self._snapshot_items_from_response(snapshot.id, query, response),
                    )
            except IntegrityError:
                session.rollback()
                existing = self.load_bucket_snapshot(
                    query,
                    bucket_start,
                    capture_scope=capture_scope,
                )
                if existing is None:
                    raise
                return existing
            session.commit()
        created = self.load_bucket_snapshot(
            query,
            bucket_start,
            capture_scope=capture_scope,
        )
        if created is None:
            raise RuntimeError("Persisted rankings snapshot could not be reloaded")
        return created

    def load_bucket_snapshot(
        self,
        query: RankingQuery,
        bucket_start: datetime,
        *,
        capture_scope: str | None = None,
    ) -> SnapshotEnvelope | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            statement = select(RankingSnapshot).where(
                RankingSnapshot.market == query.market,
                RankingSnapshot.tw_board == self._db_tw_board(query),
                RankingSnapshot.ranking_type == query.ranking_type.value,
                RankingSnapshot.bucket_start == bucket_start,
            )
            if capture_scope is not None:
                statement = statement.where(RankingSnapshot.capture_scope == capture_scope)
            snapshot = session.execute(statement).scalar_one_or_none()
            if snapshot is None:
                return None
            items = list(
                session.execute(
                    select(RankingSnapshotItem)
                    .where(RankingSnapshotItem.snapshot_id == snapshot.id)
                    .order_by(RankingSnapshotItem.rank.asc())
                ).scalars()
            )
            return SnapshotEnvelope(snapshot=snapshot, items=items)

    def load_latest_snapshot(self, query: RankingQuery) -> SnapshotEnvelope | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            snapshot = session.execute(
                select(RankingSnapshot)
                .where(
                    RankingSnapshot.market == query.market,
                    RankingSnapshot.tw_board == self._db_tw_board(query),
                    RankingSnapshot.ranking_type == query.ranking_type.value,
                )
                .order_by(
                    RankingSnapshot.bucket_start.desc(),
                    RankingSnapshot.id.desc(),
                )
                .limit(1)
            ).scalars().first()
            if snapshot is None:
                return None
            items = list(
                session.execute(
                    select(RankingSnapshotItem)
                    .where(RankingSnapshotItem.snapshot_id == snapshot.id)
                    .order_by(RankingSnapshotItem.rank.asc())
                ).scalars()
            )
            return SnapshotEnvelope(snapshot=snapshot, items=items)

    @staticmethod
    def _db_tw_board(query: RankingQuery) -> str:
        return query.tw_board or ""

    @staticmethod
    def _snapshot_items_from_response(
        snapshot_id: int,
        query: RankingQuery,
        response: RankingResponse,
    ) -> list[RankingSnapshotItem]:
        rows: list[RankingSnapshotItem] = []
        for item in response.items:
            rows.append(
                RankingSnapshotItem(
                    snapshot_id=snapshot_id,
                    rank=item.rank,
                    stock_id=item.stock.id,
                    symbol=item.stock.symbol,
                    name=item.stock.name,
                    last_price=item.lastPrice,
                    change_percent=item.changePercent,
                    volume=item.volume,
                    volume_delta=_volume_delta_value(item),
                    volume_delta_percent=item.volumeDeltaPercent,
                    net_buy=(
                        item.netBuySellShares
                        if query.ranking_type == RankingType.net_buy
                        else None
                    ),
                    net_sell=(
                        item.netBuySellShares
                        if query.ranking_type == RankingType.net_sell
                        else None
                    ),
                    data_time=item.timestamp,
                    raw_payload_json=item.model_dump(mode="json"),
                )
            )
        return rows


def _volume_delta_value(item: RankingItem) -> int | None:
    if item.volume is None or item.volumeDeltaPercent is None:
        return None
    denominator = 1 + (item.volumeDeltaPercent / 100)
    if denominator == 0:
        return None
    baseline = item.volume / denominator
    return int(round(item.volume - baseline))


ranking_snapshot_repository = RankingSnapshotRepository()
