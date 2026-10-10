from __future__ import annotations

from datetime import datetime, timezone
import logging
from collections.abc import Callable
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Literal

from app.core.config import settings
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
    RankingUniverse,
    TwBoard,
    TwRankingBoard,
)
from app.services.tw_price_limit_rankings import TW_PRICE_LIMIT_UNAVAILABLE_WARNING
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
from app.services.rankings_refresh_gate import RankingsRefreshCooldownError, RankingsRefreshGate
from app.services.rankings_service import (
    TW_TPEX_RANKING_STOCK_IDS,
    TW_TWSE_RANKING_STOCK_IDS,
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
_REFRESH_FAILURE_DETAIL = (
    "Unable to refresh rankings from the upstream source. Existing data was left unchanged."
)
_REFRESH_FALLBACK_DETAIL = (
    "Upstream rankings are unavailable. Refresh did not replace the table with a limited fallback universe."
)


class RankingsRefreshError(Exception):
    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 502,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


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
        self._refresh_gate = RankingsRefreshGate(
            lambda: settings.rankings_manual_refresh_cooldown_seconds,
        )

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
        tw_board: TwRankingBoard | None = None,
        universe: RankingUniverse | None = None,
    ) -> RankingResponse:
        _sync_repository_sessionlocal()
        started_at = perf_counter()
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        query = _normalize_query(market, RankingType(ranking_type), tw_board, universe)
        limit = _effective_limit(query, limit)
        provider = self._provider_registry.get_provider(query)

        # Price-limit lists are live-only: an empty board is a real result, and
        # these types are not written to the snapshot table or the EOD archive.
        if (
            _is_explicitly_unsupported(query)
            or _is_live_only_price_limit_query(query)
            or not ranking_snapshot_repository.is_available()
        ):
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

    def refresh_rankings(
        self,
        market: RankingMarket,
        ranking_type: RankingType | str,
        limit: int,
        now: datetime | None = None,
        tw_board: TwRankingBoard | None = None,
        universe: RankingUniverse | None = None,
    ) -> RankingResponse:
        _sync_repository_sessionlocal()
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        query = _normalize_query(market, RankingType(ranking_type), tw_board, universe)
        limit = _effective_limit(query, limit)
        key = (query.market, query.universe, query.ranking_type.value)
        try:
            leader, holder = self._refresh_gate.acquire(key)
        except RankingsRefreshCooldownError as exc:
            raise RankingsRefreshError(
                exc.detail,
                status_code=429,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
        if not leader:
            try:
                result = self._refresh_gate.join(holder)
            except RankingsRefreshError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RankingsRefreshError(_REFRESH_FAILURE_DETAIL) from exc
            if not isinstance(result, RankingResponse):
                raise RankingsRefreshError(_REFRESH_FAILURE_DETAIL)
            return result

        started_at = perf_counter()
        error: BaseException | None = None
        result: RankingResponse | None = None
        try:
            result = self._execute_refresh_rankings(query, limit, current, started_at)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._refresh_gate.release(key, holder, result=result, error=error)

    def _execute_refresh_rankings(
        self,
        query: RankingQuery,
        limit: int,
        current: datetime,
        started_at: float,
    ) -> RankingResponse:
        provider = self._provider_registry.get_provider(query)

        try:
            live = provider.fetch(
                query,
                limit=max(limit, _MAX_CAPTURE_LIMIT),
                now=current,
                allow_hardcoded_fallback=False,
                force_refresh=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Rankings refresh failed",
                extra={
                    "event": "rankings_snapshot.refresh.failed",
                    "market": query.market,
                    "ranking_type": query.ranking_type.value,
                    "tw_board": query.tw_board,
                    "universe": query.universe,
                },
            )
            raise RankingsRefreshError(_REFRESH_FAILURE_DETAIL) from exc

        if live.twRankingsScope == "fallback":
            raise RankingsRefreshError(_REFRESH_FALLBACK_DETAIL)
        if live.sourceType == RankingSourceType.unsupported:
            if _is_explicitly_unsupported(query):
                response = _truncate_response(live, limit)
                _log_rankings_response(query, response, started_at)
                return response
            raise RankingsRefreshError(
                live.unsupportedReason or _REFRESH_FAILURE_DETAIL,
            )
        if _is_live_only_price_limit_query(query):
            if live.warningMessage == TW_PRICE_LIMIT_UNAVAILABLE_WARNING:
                raise RankingsRefreshError(_REFRESH_FAILURE_DETAIL)
            response = _truncate_response(live, limit)
            _log_rankings_response(query, response, started_at)
            return response
        if not live.items:
            raise RankingsRefreshError(_REFRESH_FAILURE_DETAIL)

        live = _stamp_closed_market_refresh_timestamp(query, live, current)
        if ranking_snapshot_repository.is_available():
            bucket_start = _refresh_persist_bucket(provider, query, live, current)
            if bucket_start is not None:
                self._persist_snapshot(
                    query,
                    bucket_start,
                    live,
                    capture_scope=_CAPTURE_SCOPE_ON_DEMAND,
                    replace_existing=True,
                )
        response = _truncate_response(live, limit)
        if response.universe is None:
            response = response.model_copy(update={"universe": query.universe})
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
                captured += self._persist_scheduled_snapshot(
                    query,
                    bucket_start,
                    response,
                )
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
            captured += self._persist_scheduled_snapshot(
                query,
                bucket_start,
                response,
            )
        return captured

    def _persist_scheduled_snapshot(
        self,
        query: RankingQuery,
        bucket_start: datetime,
        response: RankingResponse,
    ) -> int:
        envelope = self._persist_snapshot(
            query,
            bucket_start,
            response,
            capture_scope=_CAPTURE_SCOPE_SCHEDULER_BOUNDED,
        )
        return 0 if envelope is None else 1

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
        replace_existing: bool = False,
    ) -> _SnapshotEnvelope | None:
        _sync_repository_sessionlocal()
        try:
            return ranking_snapshot_repository.persist_snapshot(
                query,
                bucket_start,
                response,
                capture_scope=capture_scope,
                replace_existing=replace_existing,
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
            universe=query.universe,
            items=items,
            timestamp=envelope.snapshot.created_at.astimezone(timezone.utc).isoformat(),
            source=envelope.snapshot.source,
            sourceType=RankingSourceType.cache,
            isMarketOpen=current_market_open,
            dataFreshnessLabel=freshness_label,
            warningMessage=envelope.snapshot.warning_message,
            unsupportedReason=envelope.snapshot.unsupported_reason,
            usRankingsScope=(
                _us_rankings_scope_for_query(query, envelope.snapshot.source)
                if query.market == "US"
                else None
            ),
            twRankingsScope=(
                _tw_rankings_scope_from_source(
                    envelope.snapshot.source,
                    ranking_type=query.ranking_type,
                    capture_scope=envelope.snapshot.capture_scope,
                    stock_ids=[row.stock_id for row in envelope.items],
                    tw_board=query.tw_board,
                )
                if query.market == "TW"
                else None
            ),
        )

    def _run_sampler(self, poll_seconds: int) -> None:
        from app.jobs.health import run_guarded_tick

        while not self._stop_event.wait(0):
            def _tick() -> None:
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

            run_guarded_tick(
                "rankings-snapshot-sampler",
                event="rankings_snapshot.scheduler.failed",
                tick=_tick,
            )
            if self._stop_event.wait(poll_seconds):
                return


def _normalize_query(
    market: RankingMarket,
    ranking_type: RankingType,
    tw_board: TwRankingBoard | None,
    universe: RankingUniverse | str | None = None,
) -> RankingQuery:
    if market == "TW" and (tw_board == "all" or universe == "all"):
        if ranking_type in {RankingType.limit_up, RankingType.limit_down}:
            return RankingQuery(
                market=market,
                ranking_type=ranking_type,
                tw_board="all",
                universe=None,
            )
    if market == "TW":
        board: TwBoard
        if universe in {"TWSE", "TPEx"}:
            board = universe
        elif tw_board in {"TWSE", "TPEx"}:
            board = tw_board
        else:
            board = "TWSE"
        return RankingQuery(
            market=market,
            ranking_type=ranking_type,
            tw_board=board,
            universe=board,
        )
    us_universe: RankingUniverse = (
        universe if universe in {"DJIA", "NASDAQ100"} else "DJIA"
    )
    return RankingQuery(
        market=market,
        ranking_type=ranking_type,
        tw_board=None,
        universe=us_universe,
    )


def _effective_limit(query: RankingQuery, limit: int) -> int:
    if query.universe == "DJIA":
        return min(limit, 30)
    return limit


def _closed_market_persist_bucket(now: datetime) -> datetime:
    current = now.astimezone(timezone.utc)
    minute = 0 if current.minute < 30 else 30
    return current.replace(minute=minute, second=0, microsecond=0)


def _refresh_persist_bucket(
    provider,
    query: RankingQuery,
    live: RankingResponse,
    now: datetime,
) -> datetime | None:
    if _is_tw_daily_query(query):
        return _tw_daily_bucket_start_from_response(live)
    bucket_start = provider.current_bucket_start(query, now)
    if bucket_start is not None:
        return bucket_start
    return _closed_market_persist_bucket(now)


def _stamp_closed_market_refresh_timestamp(
    query: RankingQuery,
    live: RankingResponse,
    now: datetime,
) -> RankingResponse:
    if is_market_open(query.market, now) or not live.items:
        return live
    return live.model_copy(update={"timestamp": live.items[0].timestamp})


def _truncate_response(response: RankingResponse, limit: int) -> RankingResponse:
    if len(response.items) <= limit:
        return response
    return response.model_copy(update={"items": response.items[:limit]})


def _is_explicitly_unsupported(query: RankingQuery) -> bool:
    if query.market == "US" and query.ranking_type in {
        RankingType.net_buy,
        RankingType.net_sell,
        RankingType.limit_up,
        RankingType.limit_down,
    }:
        return True
    return False


def _is_live_only_price_limit_query(query: RankingQuery) -> bool:
    """Taiwan 漲停／跌停 rankings stay on the live MIS feed and are not archived."""
    return query.market == "TW" and query.ranking_type in {
        RankingType.limit_up,
        RankingType.limit_down,
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
        universe=query.universe,
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
        usRankingsScope=(
            _us_rankings_scope_for_query(query, _QUOTE_SOURCE)
            if query.market == "US"
            else None
        ),
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
        priceLimitStatus=(
            raw_payload.get("priceLimitStatus")
            if isinstance(raw_payload, dict)
            else None
        ),
        referencePrice=(
            raw_payload.get("referencePrice")
            if isinstance(raw_payload, dict)
            else None
        ),
        limitUpPrice=(
            raw_payload.get("limitUpPrice")
            if isinstance(raw_payload, dict)
            else None
        ),
        limitDownPrice=(
            raw_payload.get("limitDownPrice")
            if isinstance(raw_payload, dict)
            else None
        ),
        timestamp=row.data_time,
    )


def _us_rankings_scope_for_query(query: RankingQuery, source: str) -> str | None:
    if query.universe == "DJIA":
        return "djia"
    if query.universe == "NASDAQ100":
        return "nasdaq100"
    return _us_rankings_scope_from_source(source)


def _us_rankings_scope_from_source(source: str) -> Literal["sp500", "yahoo", "quote"] | None:
    if "Yahoo Finance pre-ranked" in source:
        return "yahoo"
    if "S&P 500 movers" in source:
        return "sp500"
    if source in {_QUOTE_SOURCE, _VALUATION_SOURCE}:
        return "quote"
    return None


_TW_PRE_RANKED_FALLBACK_TYPES = {
    RankingType.volume,
    RankingType.change_up,
    RankingType.change_down,
}


def _hardcoded_tw_universe_ids(tw_board: str | None) -> set[str]:
    if tw_board == "TPEx":
        return set(TW_TPEX_RANKING_STOCK_IDS)
    return set(TW_TWSE_RANKING_STOCK_IDS)


def _tw_rankings_scope_from_source(
    source: str,
    *,
    ranking_type: RankingType | None = None,
    capture_scope: str | None = None,
    stock_ids: list[str] | None = None,
    tw_board: str | None = None,
) -> Literal["yahoo", "quote", "fallback"] | None:
    if "hard-coded" in source:
        return "fallback"
    if "Yahoo Finance pre-ranked" in source:
        return "yahoo"
    if source not in {_QUOTE_SOURCE, _VALUATION_SOURCE}:
        return None
    if capture_scope == _CAPTURE_SCOPE_SCHEDULER_BOUNDED:
        return "quote"
    if ranking_type in _TW_PRE_RANKED_FALLBACK_TYPES:
        if capture_scope == _CAPTURE_SCOPE_ON_DEMAND:
            return "fallback"
        return "quote"
    if ranking_type == RankingType.valuation_low_pe:
        if capture_scope != _CAPTURE_SCOPE_ON_DEMAND:
            return "quote"
        # Legacy on-demand valuation stored the generic source for both Yahoo
        # success and hardcoded fallback. New Yahoo-miss rows use the hard-coded
        # source string above. Item membership distinguishes remaining legacy rows
        # without relabeling a broader Yahoo valuation universe as fallback.
        if stock_ids and set(stock_ids) <= _hardcoded_tw_universe_ids(tw_board):
            return "fallback"
        return "yahoo"
    return "quote"


def _tw_daily_bucket_start_from_response(response: RankingResponse) -> datetime | None:
    if not response.items:
        return None
    return tw_institutional_daily_bucket_from_timestamp(response.items[0].timestamp)


def _scheduled_queries() -> list[RankingQuery]:
    # limit_up / limit_down are live-only MIS rankings and are intentionally
    # omitted here so they are not snapshotted or copied into the EOD archive.
    queries: list[RankingQuery] = []
    for universe in ("DJIA", "NASDAQ100"):
        queries.extend(
            [
                RankingQuery("US", RankingType.volume_up, universe=universe),
                RankingQuery("US", RankingType.volume_down, universe=universe),
                RankingQuery("US", RankingType.volume, universe=universe),
                RankingQuery("US", RankingType.change_up, universe=universe),
                RankingQuery("US", RankingType.change_down, universe=universe),
                RankingQuery("US", RankingType.valuation_low_pe, universe=universe),
            ]
        )
    for board in ("TWSE", "TPEx"):
        queries.extend(
            [
                RankingQuery("TW", RankingType.volume_up, board, universe=board),
                RankingQuery("TW", RankingType.volume_down, board, universe=board),
                RankingQuery("TW", RankingType.volume, board, universe=board),
                RankingQuery("TW", RankingType.change_up, board, universe=board),
                RankingQuery("TW", RankingType.change_down, board, universe=board),
                RankingQuery("TW", RankingType.valuation_low_pe, board, universe=board),
                RankingQuery("TW", RankingType.net_buy, board, universe=board),
                RankingQuery("TW", RankingType.net_sell, board, universe=board),
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
            "universe": query.universe,
            "duration_ms": elapsed_ms(started_at),
            "item_count": len(response.items),
            "source_type": response.sourceType.value,
            "is_market_open": response.isMarketOpen,
        },
    )


rankings_snapshot_service = RankingsSnapshotService()
