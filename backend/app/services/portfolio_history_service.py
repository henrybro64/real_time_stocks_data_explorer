from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Lock, Thread
from time import perf_counter

from app.core.diagnostics import elapsed_ms
from app.db.models import PortfolioHistorySnapshot, PortfolioPosition
from app.models.quote import QuoteRecord
from app.repositories.portfolio_history_repository import (
    portfolio_history_repository,
)
from app.services.fx_service import get_usd_twd_fx_snapshot
from app.services.market_clock import iter_open_snapshot_windows
from app.services.portfolio_service import portfolio_service
from app.services.quote_service import quote_service


logger = logging.getLogger(__name__)

_RANGE_TO_DELTA = {
    "1D": timedelta(days=1),
    "1W": timedelta(weeks=1),
    "1M": timedelta(days=31),
    "3M": timedelta(days=93),
    "6M": timedelta(days=186),
    "1Y": timedelta(days=366),
}


@dataclass(frozen=True)
class PortfolioHistoryPoint:
    timestamp: str
    source_window: str
    fx_rate: float
    value: float


@dataclass(frozen=True)
class PortfolioHistorySeries:
    currency: str
    points: list[PortfolioHistoryPoint]


@dataclass(frozen=True)
class SnapshotMetrics:
    fx_rate: float
    holdings_value_usd: float
    holdings_value_twd: float
    combined_assets_usd: float
    combined_assets_twd: float


class PortfolioHistoryService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start_background_sampler(self, poll_seconds: int) -> None:
        if not portfolio_history_repository.is_available():
            logger.info(
                "Portfolio history sampler skipped because the database is unavailable",
                extra={"event": "portfolio_history.sampler.skipped", "reason": "database_unavailable"},
            )
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug(
                    "Portfolio history sampler was already running",
                    extra={"event": "portfolio_history.sampler.already_running"},
                )
                return
            self._stop_event = Event()
            self._thread = Thread(
                target=self._run_sampler,
                args=(poll_seconds,),
                daemon=True,
                name="portfolio-history-sampler",
            )
            self._thread.start()
        logger.info(
            "Portfolio history sampler started",
            extra={
                "event": "portfolio_history.sampler.started",
                "job_name": "portfolio-history-sampler",
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
            "Portfolio history sampler stopped",
            extra={
                "event": "portfolio_history.sampler.stopped",
                "job_name": "portfolio-history-sampler",
            },
        )

    def capture_due_snapshots_for_all_users(
        self,
        now: datetime | None = None,
        *,
        trigger: str = "manual",
    ) -> int:
        started_at = perf_counter()
        if not portfolio_history_repository.is_available():
            return 0
        open_windows = iter_open_snapshot_windows(now)
        if not open_windows:
            logger.debug(
                "Portfolio history capture skipped because no snapshot window is open",
                extra={
                    "event": "portfolio_history.capture_all.skipped",
                    "trigger": trigger,
                    "reason": "no_open_snapshot_windows",
                },
            )
            return 0
        user_ids = portfolio_history_repository.list_user_ids()
        total = 0
        for user_id in user_ids:
            total += self.capture_due_snapshots_for_user(
                user_id,
                now=now,
                open_windows=open_windows,
                trigger=trigger,
            )
        logger.info(
            "Portfolio history capture completed for all users",
            extra={
                "event": "portfolio_history.capture_all.completed",
                "job_name": "portfolio_history_capture_all_users",
                "trigger": trigger,
                "user_count": len(user_ids),
                "captured_count": total,
                "duration_ms": elapsed_ms(started_at),
            },
        )
        return total

    def capture_due_snapshots_for_user(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        open_windows: list[tuple[str, datetime]] | None = None,
        trigger: str = "manual",
    ) -> int:
        started_at = perf_counter()
        if not portfolio_history_repository.is_available():
            return 0
        active_windows = open_windows if open_windows is not None else iter_open_snapshot_windows(now)
        if not active_windows:
            logger.debug(
                "Portfolio history capture skipped for user",
                extra={
                    "event": "portfolio_history.capture_user.skipped",
                    "user_id": user_id,
                    "trigger": trigger,
                    "reason": "no_open_snapshot_windows",
                },
            )
            return 0

        metrics = self._build_snapshot_metrics(user_id)
        captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        touched = portfolio_history_repository.upsert_snapshots(
            user_id=user_id,
            windows=active_windows,
            captured_at=captured_at,
            fx_rate=metrics.fx_rate,
            holdings_value_usd=metrics.holdings_value_usd,
            holdings_value_twd=metrics.holdings_value_twd,
            combined_assets_usd=metrics.combined_assets_usd,
            combined_assets_twd=metrics.combined_assets_twd,
        )
        logger.log(
            logging.INFO if touched > 0 else logging.DEBUG,
            "Portfolio history capture completed for user",
            extra={
                "event": "portfolio_history.capture_user.completed",
                "user_id": user_id,
                "trigger": trigger,
                "window_count": len(active_windows),
                "captured_count": touched,
                "duration_ms": elapsed_ms(started_at),
            },
        )
        return touched

    def list_metric_history(
        self,
        user_id: str,
        *,
        metric: str,
        range_name: str,
        now: datetime | None = None,
    ) -> dict[str, PortfolioHistorySeries]:
        if not portfolio_history_repository.is_available():
            raise RuntimeError("DATABASE_URL is not configured")
        self.capture_due_snapshots_for_user(user_id, now=now, trigger="history_read")

        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = now_utc - _range_delta(range_name)
        snapshots = portfolio_history_repository.list_snapshots_since(user_id, cutoff)

        return {
            "USD": PortfolioHistorySeries(
                currency="USD",
                points=[
                    PortfolioHistoryPoint(
                        timestamp=snapshot.snapshot_hour.isoformat(),
                        source_window=snapshot.source_window,
                        fx_rate=float(snapshot.usd_twd_rate),
                        value=_metric_value(snapshot, metric, "USD"),
                    )
                    for snapshot in snapshots
                ],
            ),
            "TWD": PortfolioHistorySeries(
                currency="TWD",
                points=[
                    PortfolioHistoryPoint(
                        timestamp=snapshot.snapshot_hour.isoformat(),
                        source_window=snapshot.source_window,
                        fx_rate=float(snapshot.usd_twd_rate),
                        value=_metric_value(snapshot, metric, "TWD"),
                    )
                    for snapshot in snapshots
                ],
            ),
        }

    def _run_sampler(self, poll_seconds: int) -> None:
        while not self._stop_event.wait(poll_seconds):
            try:
                self.capture_due_snapshots_for_all_users(trigger="background_sampler")
            except Exception:
                logger.exception(
                    "Portfolio history sampler failed",
                    extra={
                        "event": "portfolio_history.sampler.failed",
                        "job_name": "portfolio-history-sampler",
                        "poll_seconds": poll_seconds,
                    },
                )

    def _build_snapshot_metrics(self, user_id: str) -> SnapshotMetrics:
        positions = portfolio_service.list_positions(user_id)
        assets = portfolio_service.list_assets(user_id)
        fx_snapshot = get_usd_twd_fx_snapshot()
        usd_twd_rate = float(fx_snapshot["rate"])

        holdings_entries: list[tuple[str, float]] = []
        for position in positions:
            stock = quote_service.get_stock(position.stock_id)
            if stock is None:
                continue
            quote = self._resolve_quote(position)
            if quote is not None:
                market_value = round(position.quantity * quote.last_price, 2)
            else:
                market_value = round(position.quantity * float(position.average_cost), 2)
            holdings_entries.append((stock.currency, market_value))

        holdings_totals = portfolio_service.summarize_currency_buckets(
            holdings_entries,
            usd_twd_rate,
        )
        all_entries = list(holdings_entries)
        for asset in assets:
            all_entries.append((asset.currency, round(float(asset.value), 2)))
        combined_totals = portfolio_service.summarize_currency_buckets(
            all_entries,
            usd_twd_rate,
        )
        return SnapshotMetrics(
            fx_rate=usd_twd_rate,
            holdings_value_usd=holdings_totals["combinedUsd"],
            holdings_value_twd=holdings_totals["combinedTwd"],
            combined_assets_usd=combined_totals["combinedUsd"],
            combined_assets_twd=combined_totals["combinedTwd"],
        )

    def _resolve_quote(self, position: PortfolioPosition) -> QuoteRecord | None:
        try:
            return quote_service.get_quote(position.stock_id)
        except Exception as exc:
            logger.warning(
                "Snapshot quote lookup failed",
                extra={
                    "event": "portfolio_history.snapshot.quote_lookup_failed",
                    "stock_id": position.stock_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None


def _range_delta(range_name: str) -> timedelta:
    try:
        return _RANGE_TO_DELTA[range_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported history range: {range_name}") from exc


def _metric_value(
    snapshot: PortfolioHistorySnapshot,
    metric: str,
    currency: str,
) -> float:
    if metric == "holdings":
        return (
            float(snapshot.holdings_value_usd)
            if currency == "USD"
            else float(snapshot.holdings_value_twd)
        )
    if metric == "combinedAssets":
        return (
            float(snapshot.combined_assets_usd)
            if currency == "USD"
            else float(snapshot.combined_assets_twd)
        )
    raise ValueError(f"Unsupported history metric: {metric}")


portfolio_history_service = PortfolioHistoryService()
