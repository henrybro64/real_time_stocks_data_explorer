from __future__ import annotations

from datetime import datetime, timezone
import logging
from collections.abc import Callable
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Literal

from app.core.diagnostics import elapsed_ms
from app.db.models import RankingSnapshotItem
from app.db.session import SessionLocal as DefaultSessionLocal
from app.repositories.ranking_snapshot_repository import (
    SnapshotEnvelope as _SnapshotEnvelope,
    ranking_snapshot_repository,
)
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
    TwBoard,
)
from app.schemas.stock import StockResponse
from app.services.market_clock import (
    is_market_open,
    tw_institutional_daily_bucket_from_timestamp,
    tw_institutional_daily_snapshot_bucket,
)
from app.services.ranking_data_providers import (
    RankingMarket,
    RankingProviderRegistry,
    RankingQuery,
    ranking_provider_registry,
    scheduler_ranking_provider_registry,
)


logger = logging.getLogger(__name__)

SessionLocal = DefaultSessionLocal

_MAX_CAPTURE_LIMIT = 50
_CAPTURE_SCOPE_ON_DEMAND = "on_demand"
_CAPTURE_SCOPE_SCHEDULER_BOUNDED = "scheduler_bounded"
_MARKET_CLOSED_FRESHNESS = "Market is closed. Showing latest available free snapshot."
_PROVIDER_FAILURE_FRESHNESS = (
    "Free data source temporarily unavailable. Showing latest available snapshot if available."
)
_CURRENT_BUCKET_CACHE_FRESHNESS = (
    "Showing the latest saved 30-minute free snapshot for this market window."
)
_DAILY_CACHE_FRESHNESS = "Showing the latest saved TW institutional daily snapshot."
_NO_SNAPSHOT_UNAVAILABLE_FRESHNESS = (
    "Free data source temporarily unavailable. No cached snapshot is available yet."
)
_QUOTE_SOURCE = "Quote rankings from the app's free best-effort quote providers."
_VALUATION_SOURCE = (
    "Valuation rankings use the highest-volume current quote candidates, then sort by "
    "P/E from the app's available valuation sources."
)
_TWSE_SOURCE = "TWSE T86 daily institutional data"
_TPEX_SOURCE = "TPEx institutional daily data"
_UNSUPPORTED_SOURCE = "Unsupported in this free-only app"
_QUOTE_WARNING = "Free unofficial quote source; data may be delayed or unavailable."
_TWSE_WARNING = "台股三大法人買賣超為日資料，非盤中即時資料。"
_TPEX_WARNING = "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"


def _sync_repository_sessionlocal() -> None:
    ranking_snapshot_repository.set_session_factory_override(SessionLocal)


class RankingsSnapshotService:
    def __init__(
        self,
        provider_registry: RankingProviderRegistry | None = None,
        scheduler_provider_registry: RankingProviderRegistry | None = None,
    ) -> None:
        self._provider_registry = provider_registry or ranking_provider_registry
        self._scheduler_provider_registry = (
            scheduler_provider_registry or scheduler_ranking_provider_registry
        )
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start_background_sampler(self, poll_seconds: int) -> None:
        _sync_repository_sessionlocal()
        if not ranking_snapshot_repository.is_available():
            logger.info(
                "Rankings snapshot sampler skipped because the database is unavailable",
                extra={"event": "rankings_snapshot.sampler.skipped", "reason": "database_unavailable"},
            )
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = Event()
            self._thread = Thread(
                target=self._run_sampler,
                args=(poll_seconds,),
                daemon=True,
                name="rankings-snapshot-sampler",
            )
            self._thread.start()
        logger.info(
            "Rankings snapshot sampler started",
            extra={
                "event": "rankings_snapshot.sampler.started",
                "job_name": "rankings-snapshot-sampler",
                "poll_seconds": poll_seconds,
            },
        )

    def stop_background_sampler(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._thread = None
            self._stop_event.set()
        thread.join(timeout=1.0)
        logger.info(
            "Rankings snapshot sampler stopped",
            extra={
                "event": "rankings_snapshot.sampler.stopped",
                "job_name": "rankings-snapshot-sampler",
            },
        )

    def get_rankings(
        self,
        market: RankingMarket,
        ranking_type: RankingType | str,
        limit: int,
        now: datetime | None = None,
        tw_board: TwBoard | None = None,
    ) -> RankingResponse:
        _sync_repository_sessionlocal()
        started_at = perf_counter()
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        query = _normalize_query(market, RankingType(ranking_type), tw_board)
        provider = self._provider_registry.get_provider(query)

        if _is_explicitly_unsupported(query) or not ranking_snapshot_repository.is_available():
            response = _truncate_response(
                provider.fetch(query, limit=limit, now=current),
                limit,
            )
            _log_rankings_response(query, response, started_at)
            return response

        if _is_tw_daily_query(query):
            response = self._get_tw_daily_institutional_rankings(
                query,
                limit=limit,
                now=current,
            )
            _log_rankings_response(query, response, started_at)
            return response

        bucket_start = provider.current_bucket_start(query, current)
        if bucket_start is not None:
            existing = self._load_bucket_snapshot(
                query,
                bucket_start,
                capture_scope=_CAPTURE_SCOPE_ON_DEMAND,
            )
            if existing is None:
                existing = self._load_bucket_snapshot(
                    query,
                    bucket_start,
                    capture_scope=_CAPTURE_SCOPE_SCHEDULER_BOUNDED,
                )
            if existing is not None:
                response = self._response_from_snapshot(
                    query,
                    existing,
                    current,
                    limit=limit,
                    freshness_label=_cache_freshness_label(query, current),
                )
                _log_rankings_response(query, response, started_at)
                return response

            try:
                live = provider.fetch(
                    query,
                    limit=max(limit, _MAX_CAPTURE_LIMIT),
                    now=current,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Rankings snapshot capture failed",
                    extra={
                        "event": "rankings_snapshot.capture.failed",
                        "market": query.market,
                        "ranking_type": query.ranking_type.value,
                        "tw_board": query.tw_board,
                    },
                )
                latest = self._load_latest_snapshot(query)
                if latest is not None:
                    response = self._response_from_snapshot(
                        query,
                        latest,
                        current,
                        limit=limit,
                        freshness_label=_PROVIDER_FAILURE_FRESHNESS,
                    )
                    _log_rankings_response(query, response, started_at)
                    return response
                response = _unavailable_response(query, current)
                _log_rankings_response(query, response, started_at)
                return response

            if live.sourceType == RankingSourceType.unsupported or not live.items:
                latest = self._load_latest_snapshot(query)
                if latest is not None:
                    response = self._response_from_snapshot(
                        query,
                        latest,
                        current,
                        limit=limit,
                        freshness_label=_PROVIDER_FAILURE_FRESHNESS,
                    )
                    _log_rankings_response(query, response, started_at)
                    return response
                response = _truncate_response(live, limit)
                if response.sourceType != RankingSourceType.unsupported:
                    response = _unavailable_response(query, current)
                _log_rankings_response(query, response, started_at)
                return response

            self._persist_snapshot(
                query,
                bucket_start,
                live,
                capture_scope=_CAPTURE_SCOPE_ON_DEMAND,
            )
            response = _truncate_response(live, limit)
            _log_rankings_response(query, response, started_at)
            return response

        latest = self._load_latest_snapshot(query)
        if latest is not None:
            response = self._response_from_snapshot(
                query,
                latest,
                current,
                limit=limit,
                freshness_label=_MARKET_CLOSED_FRESHNESS,
            )
            _log_rankings_response(query, response, started_at)
            return response

        response = _unavailable_response(query, current)
        _log_rankings_response(query, response, started_at)
        return response

    def capture_scheduled_snapshots(
        self,
        now: datetime | None = None,
        *,
        trigger: str = "manual",
    ) -> int:
        _sync_repository_sessionlocal()
        if not ranking_snapshot_repository.is_available():
            return 0
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        captured = 0
        for query in _scheduled_queries():
            provider = self._scheduler_provider_registry.get_provider(query)
            if _is_tw_daily_query(query):
                if self._load_applicable_tw_daily_snapshot(
                    query,
                    current,
                    capture_scopes=(_CAPTURE_SCOPE_SCHEDULER_BOUNDED,),
                ) is not None:
                    continue
                try:
                    response = provider.fetch(
                        query,
                        limit=_MAX_CAPTURE_LIMIT,
                        now=current,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Scheduled rankings capture failed",
                        extra={
                            "event": "rankings_snapshot.scheduler.capture_failed",
                            "trigger": trigger,
                            "market": query.market,
                            "ranking_type": query.ranking_type.value,
                            "tw_board": query.tw_board,
                        },
                    )
                    continue
                if response.sourceType == RankingSourceType.unsupported or not response.items:
                    continue
                bucket_start = _tw_daily_bucket_start_from_response(response)
                if bucket_start is None:
                    continue
                if (
                    self._load_bucket_snapshot(
                        query,
                        bucket_start,
                        capture_scope=_CAPTURE_SCOPE_SCHEDULER_BOUNDED,
                    )
                    is not None
                ):
                    continue
                if (
                    self._persist_snapshot(
                        query,
                        bucket_start,
                        response,
                        capture_scope=_CAPTURE_SCOPE_SCHEDULER_BOUNDED,
                    )
                    is not None
                ):
                    captured += 1
                continue

            bucket_start = provider.current_bucket_start(query, current)
            if bucket_start is None or self._load_bucket_snapshot(
                query,
                bucket_start,
                capture_scope=_CAPTURE_SCOPE_SCHEDULER_BOUNDED,
            ) is not None:
                continue
            try:
                response = provider.fetch(
                    query,
                    limit=_MAX_CAPTURE_LIMIT,
                    now=current,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Scheduled rankings capture failed",
                    extra={
                        "event": "rankings_snapshot.scheduler.capture_failed",
                        "trigger": trigger,
                        "market": query.market,
                        "ranking_type": query.ranking_type.value,
                        "tw_board": query.tw_board,
                    },
                )
                continue
            if response.sourceType == RankingSourceType.unsupported or not response.items:
                continue
            if (
                self._persist_snapshot(
                    query,
                    bucket_start,
                    response,
                    capture_scope=_CAPTURE_SCOPE_SCHEDULER_BOUNDED,
                )
                is not None
            ):
                captured += 1
        return captured

    def _get_tw_daily_institutional_rankings(
        self,
        query: RankingQuery,
        *,
        limit: int,
        now: datetime,
    ) -> RankingResponse:
        cached = self._load_applicable_tw_daily_snapshot(query, now)
        if cached is not None:
            return self._response_from_snapshot(
                query,
                cached,
                now,
                limit=limit,
                freshness_label=_cache_freshness_label(query, now),
            )

        provider = self._provider_registry.get_provider(query)
        try:
            live = provider.fetch(
                query,
                limit=max(limit, _MAX_CAPTURE_LIMIT),
                now=now,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Rankings snapshot capture failed",
                extra={
                    "event": "rankings_snapshot.capture.failed",
                    "market": query.market,
                    "ranking_type": query.ranking_type.value,
                    "tw_board": query.tw_board,
                },
            )
            latest = self._load_latest_snapshot(query)
            if latest is not None:
                return self._response_from_snapshot(
                    query,
                    latest,
                    now,
                    limit=limit,
                    freshness_label=_PROVIDER_FAILURE_FRESHNESS,
                )
            return _unavailable_response(query, now)

        if live.sourceType == RankingSourceType.unsupported or not live.items:
            latest = self._load_latest_snapshot(query)
            if latest is not None:
                return self._response_from_snapshot(
                    query,
                    latest,
                    now,
                    limit=limit,
                    freshness_label=_PROVIDER_FAILURE_FRESHNESS,
                )
            return _truncate_response(live, limit)

        bucket_start = _tw_daily_bucket_start_from_response(live)
        if bucket_start is not None:
            self._persist_snapshot(
                query,
                bucket_start,
                live,
                capture_scope=_CAPTURE_SCOPE_ON_DEMAND,
            )

        return _truncate_response(live, limit)

    def _load_applicable_tw_daily_snapshot(
        self,
        query: RankingQuery,
        now: datetime,
        *,
        capture_scopes: tuple[str, ...] = (
            _CAPTURE_SCOPE_ON_DEMAND,
            _CAPTURE_SCOPE_SCHEDULER_BOUNDED,
        ),
    ) -> _SnapshotEnvelope | None:
        weekend_bucket = tw_institutional_daily_snapshot_bucket(
            now,
            include_exchange_holidays=False,
        )
        if weekend_bucket is not None:
            existing = self._load_tw_daily_snapshot_for_bucket(
                query,
                weekend_bucket,
                capture_scopes=capture_scopes,
            )
            if existing is not None:
                return existing
        expected_bucket = tw_institutional_daily_snapshot_bucket(now)
        if expected_bucket is not None and expected_bucket != weekend_bucket:
            existing = self._load_tw_daily_snapshot_for_bucket(
                query,
                expected_bucket,
                capture_scopes=capture_scopes,
            )
            if existing is not None:
                return existing
        if expected_bucket is None:
            return self._load_latest_snapshot(query)
        return None

    def _load_tw_daily_snapshot_for_bucket(
        self,
        query: RankingQuery,
        bucket_start: datetime,
        *,
        capture_scopes: tuple[str, ...],
    ) -> _SnapshotEnvelope | None:
        for capture_scope in capture_scopes:
            existing = self._load_bucket_snapshot(
                query,
                bucket_start,
                capture_scope=capture_scope,
            )
            if existing is not None:
                return existing
        return None

    def _persist_snapshot(
        self,
        query: RankingQuery,
        bucket_start: datetime,
        response: RankingResponse,
        *,
        capture_scope: str = _CAPTURE_SCOPE_ON_DEMAND,
    ) -> _SnapshotEnvelope | None:
        _sync_repository_sessionlocal()
        try:
            return ranking_snapshot_repository.persist_snapshot(
                query,
                bucket_start,
                response,
                capture_scope=capture_scope,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Rankings snapshot persist failed",
                extra={
                    "event": "rankings_snapshot.persist.failed",
                    "market": query.market,
                    "ranking_type": query.ranking_type.value,
                    "tw_board": query.tw_board,
                    "capture_scope": capture_scope,
                },
            )
            return None

    def _load_bucket_snapshot(
        self,
        query: RankingQuery,
        bucket_start: datetime,
        *,
        capture_scope: str | None = None,
    ) -> _SnapshotEnvelope | None:
        return self._read_snapshot(
            query,
            "load_bucket",
            lambda: ranking_snapshot_repository.load_bucket_snapshot(
                query,
                bucket_start,
                capture_scope=capture_scope,
            ),
        )

    def _load_latest_snapshot(
        self,
        query: RankingQuery,
    ) -> _SnapshotEnvelope | None:
        return self._read_snapshot(
            query,
            "load_latest",
            lambda: ranking_snapshot_repository.load_latest_snapshot(query),
        )

    def _read_snapshot(
        self,
        query: RankingQuery,
        operation: str,
        loader: Callable[[], _SnapshotEnvelope | None],
    ) -> _SnapshotEnvelope | None:
        _sync_repository_sessionlocal()
        if not ranking_snapshot_repository.is_available():
            return None
        try:
            return loader()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Rankings snapshot read failed",
                extra={
                    "event": "rankings_snapshot.read.failed",
                    "operation": operation,
                    "market": query.market,
                    "ranking_type": query.ranking_type.value,
                    "tw_board": query.tw_board,
                },
            )
            return None

    def _response_from_snapshot(
        self,
        query: RankingQuery,
        envelope: _SnapshotEnvelope,
        now: datetime,
        *,
        limit: int,
        freshness_label: str,
    ) -> RankingResponse:
        current_market_open = is_market_open(query.market, now)
        items = [
            _ranking_item_from_snapshot(row)
            for row in envelope.items[:limit]
        ]
        return RankingResponse(
            market=query.market,
            rankingType=query.ranking_type,
            twBoard=query.tw_board if query.market == "TW" else None,
            items=items,
            timestamp=envelope.snapshot.created_at.astimezone(timezone.utc).isoformat(),
            source=envelope.snapshot.source,
            sourceType=RankingSourceType.cache,
            isMarketOpen=current_market_open,
            dataFreshnessLabel=freshness_label,
            warningMessage=envelope.snapshot.warning_message,
            unsupportedReason=envelope.snapshot.unsupported_reason,
            usRankingsScope=_us_rankings_scope_from_source(envelope.snapshot.source),
        )

    def _run_sampler(self, poll_seconds: int) -> None:
        while not self._stop_event.wait(0):
            started_at = perf_counter()
            captured = self.capture_scheduled_snapshots(trigger="scheduler")
            logger.info(
                "Scheduled rankings capture completed",
                extra={
                    "event": "rankings_snapshot.scheduler.completed",
                    "captured_count": captured,
                    "duration_ms": elapsed_ms(started_at),
                },
            )
            if self._stop_event.wait(poll_seconds):
                return


def _normalize_query(
    market: RankingMarket,
    ranking_type: RankingType,
    tw_board: TwBoard | None,
) -> RankingQuery:
    effective_tw_board = tw_board if market == "TW" else None
    if market == "TW" and effective_tw_board is None:
        effective_tw_board = "TWSE"
    return RankingQuery(
        market=market,
        ranking_type=ranking_type,
        tw_board=effective_tw_board,
    )
def _truncate_response(response: RankingResponse, limit: int) -> RankingResponse:
    if len(response.items) <= limit:
        return response
    return response.model_copy(update={"items": response.items[:limit]})


def _is_explicitly_unsupported(query: RankingQuery) -> bool:
    return query.market == "US" and query.ranking_type in {
        RankingType.net_buy,
        RankingType.net_sell,
    }


def _is_tw_daily_query(query: RankingQuery) -> bool:
    return query.market == "TW" and query.ranking_type in {
        RankingType.net_buy,
        RankingType.net_sell,
    }


def _cache_freshness_label(
    query: RankingQuery,
    now: datetime,
) -> str:
    if _is_tw_daily_query(query):
        return _DAILY_CACHE_FRESHNESS
    if not is_market_open(query.market, now):
        return _MARKET_CLOSED_FRESHNESS
    return _CURRENT_BUCKET_CACHE_FRESHNESS


def _default_source_for_query(query: RankingQuery) -> str:
    if _is_explicitly_unsupported(query):
        return _UNSUPPORTED_SOURCE
    if _is_tw_daily_query(query):
        return _TPEX_SOURCE if query.tw_board == "TPEx" else _TWSE_SOURCE
    if query.ranking_type == RankingType.valuation_low_pe:
        return _VALUATION_SOURCE
    return _QUOTE_SOURCE


def _default_source_type_for_query(query: RankingQuery) -> RankingSourceType:
    if _is_explicitly_unsupported(query):
        return RankingSourceType.unsupported
    if _is_tw_daily_query(query):
        return RankingSourceType.free_public
    return RankingSourceType.free_unofficial


def _default_warning_for_query(query: RankingQuery) -> str | None:
    if _is_tw_daily_query(query):
        return _TPEX_WARNING if query.tw_board == "TPEx" else _TWSE_WARNING
    if _is_explicitly_unsupported(query):
        return None
    return _QUOTE_WARNING


def _unavailable_response(
    query: RankingQuery,
    now: datetime,
) -> RankingResponse:
    return RankingResponse(
        market=query.market,
        rankingType=query.ranking_type,
        twBoard=query.tw_board if query.market == "TW" else None,
        items=[],
        timestamp=now.astimezone(timezone.utc).isoformat(),
        source=_default_source_for_query(query),
        sourceType=_default_source_type_for_query(query),
        isMarketOpen=is_market_open(query.market, now),
        dataFreshnessLabel=_NO_SNAPSHOT_UNAVAILABLE_FRESHNESS,
        warningMessage=_default_warning_for_query(query),
        unsupportedReason=(
            "US institutional net-buy/net-sell is not available in this free-only app."
            if _is_explicitly_unsupported(query)
            else None
        ),
        usRankingsScope="quote" if query.market == "US" else None,
    )
def _ranking_item_from_snapshot(row: RankingSnapshotItem) -> RankingItem:
    raw_payload = row.raw_payload_json if isinstance(row.raw_payload_json, dict) else {}
    raw_stock = raw_payload.get("stock") if isinstance(raw_payload, dict) else None
    if isinstance(raw_stock, dict):
        stock = StockResponse.model_validate(raw_stock)
    else:
        stock = StockResponse(
            id=row.stock_id,
            market=row.stock_id.split(":", 1)[0],
            symbol=row.symbol,
            name=row.name,
            exchange="TWSE" if row.stock_id.startswith("TW:") else "NASDAQ",
            currency="TWD" if row.stock_id.startswith("TW:") else "USD",
            timezone="Asia/Taipei" if row.stock_id.startswith("TW:") else "America/New_York",
            nameEn=None,
            nameZhTw=None,
        )
    return RankingItem(
        rank=row.rank,
        stock=stock,
        lastPrice=row.last_price,
        changePercent=row.change_percent,
        volume=row.volume,
        volumeDeltaPercent=row.volume_delta_percent,
        netBuySellShares=row.net_buy if row.net_buy is not None else row.net_sell,
        peRatio=(
            raw_payload.get("peRatio")
            if isinstance(raw_payload, dict)
            else None
        ),
        peRatioType=(
            raw_payload.get("peRatioType")
            if isinstance(raw_payload, dict)
            else None
        ),
        peRatioSource=(
            raw_payload.get("peRatioSource")
            if isinstance(raw_payload, dict)
            else None
        ),
        valuationAsOf=(
            raw_payload.get("valuationAsOf")
            if isinstance(raw_payload, dict)
            else None
        ),
        valuationWarning=(
            raw_payload.get("valuationWarning")
            if isinstance(raw_payload, dict)
            else None
        ),
        timestamp=row.data_time,
    )


def _us_rankings_scope_from_source(source: str) -> Literal["sp500", "yahoo", "quote"] | None:
    if "Yahoo Finance pre-ranked" in source:
        return "yahoo"
    if "S&P 500 movers" in source:
        return "sp500"
    if source in {_QUOTE_SOURCE, _VALUATION_SOURCE}:
        return "quote"
    return None


def _tw_daily_bucket_start_from_response(response: RankingResponse) -> datetime | None:
    if not response.items:
        return None
    return tw_institutional_daily_bucket_from_timestamp(response.items[0].timestamp)


def _scheduled_queries() -> list[RankingQuery]:
    queries: list[RankingQuery] = [
        RankingQuery("US", RankingType.volume_up),
        RankingQuery("US", RankingType.volume_down),
        RankingQuery("US", RankingType.volume),
        RankingQuery("US", RankingType.change_up),
        RankingQuery("US", RankingType.change_down),
        RankingQuery("US", RankingType.valuation_low_pe),
    ]
    for board in ("TWSE", "TPEx"):
        queries.extend(
            [
                RankingQuery("TW", RankingType.volume_up, board),
                RankingQuery("TW", RankingType.volume_down, board),
                RankingQuery("TW", RankingType.volume, board),
                RankingQuery("TW", RankingType.change_up, board),
                RankingQuery("TW", RankingType.change_down, board),
                RankingQuery("TW", RankingType.valuation_low_pe, board),
                RankingQuery("TW", RankingType.net_buy, board),
                RankingQuery("TW", RankingType.net_sell, board),
            ]
        )
    return queries


def _log_rankings_response(
    query: RankingQuery,
    response: RankingResponse,
    started_at: float,
) -> None:
    logger.info(
        "Rankings snapshot request completed",
        extra={
            "event": "rankings_snapshot.request.completed",
            "market": query.market,
            "ranking_type": query.ranking_type.value,
            "tw_board": query.tw_board,
            "duration_ms": elapsed_ms(started_at),
            "item_count": len(response.items),
            "source_type": response.sourceType.value,
            "is_market_open": response.isMarketOpen,
        },
    )


rankings_snapshot_service = RankingsSnapshotService()
