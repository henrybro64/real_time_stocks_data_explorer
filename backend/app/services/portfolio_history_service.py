from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Lock, Thread

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import PortfolioHistorySnapshot, PortfolioPosition, User
from app.db.session import SessionLocal
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
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
        if SessionLocal is None:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = Event()
            self._thread = Thread(
                target=self._run_sampler,
                args=(poll_seconds,),
                daemon=True,
                name="portfolio-history-sampler",
            )
            self._thread.start()

    def stop_background_sampler(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._thread = None
            self._stop_event.set()
        thread.join(timeout=1.0)

    def capture_due_snapshots_for_all_users(
        self,
        now: datetime | None = None,
    ) -> int:
        if SessionLocal is None:
            return 0
        open_windows = iter_open_snapshot_windows(now)
        if not open_windows:
            return 0
        with SessionLocal() as session:
            user_ids = list(session.execute(select(User.id)).scalars())
        total = 0
        for user_id in user_ids:
            total += self.capture_due_snapshots_for_user(
                user_id,
                now=now,
                open_windows=open_windows,
            )
        return total

    def capture_due_snapshots_for_user(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        open_windows: list[tuple[str, datetime]] | None = None,
    ) -> int:
        if SessionLocal is None:
            return 0
        active_windows = open_windows if open_windows is not None else iter_open_snapshot_windows(now)
        if not active_windows:
            return 0

        metrics = self._build_snapshot_metrics(user_id)
        captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        with SessionLocal() as session:
            touched = 0
            for source_window, snapshot_hour in active_windows:
                snapshot = PortfolioHistorySnapshot(
                    user_id=user_id,
                    snapshot_hour=snapshot_hour,
                    source_window=source_window,
                    captured_at=captured_at,
                    usd_twd_rate=metrics.fx_rate,
                    holdings_value_usd=metrics.holdings_value_usd,
                    holdings_value_twd=metrics.holdings_value_twd,
                    combined_assets_usd=metrics.combined_assets_usd,
                    combined_assets_twd=metrics.combined_assets_twd,
                )
                try:
                    with session.begin_nested():
                        session.add(snapshot)
                        session.flush()
                except IntegrityError:
                    snapshot = session.execute(
                        select(PortfolioHistorySnapshot).where(
                            PortfolioHistorySnapshot.user_id == user_id,
                            PortfolioHistorySnapshot.snapshot_hour == snapshot_hour,
                            PortfolioHistorySnapshot.source_window == source_window,
                        )
                    ).scalar_one()
                    snapshot.captured_at = captured_at
                    snapshot.usd_twd_rate = metrics.fx_rate
                    snapshot.holdings_value_usd = metrics.holdings_value_usd
                    snapshot.holdings_value_twd = metrics.holdings_value_twd
                    snapshot.combined_assets_usd = metrics.combined_assets_usd
                    snapshot.combined_assets_twd = metrics.combined_assets_twd
                touched += 1
            session.commit()
        return touched

    def list_metric_history(
        self,
        user_id: str,
        *,
        metric: str,
        range_name: str,
        now: datetime | None = None,
    ) -> dict[str, PortfolioHistorySeries]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        self.capture_due_snapshots_for_user(user_id, now=now)

        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = now_utc - _range_delta(range_name)

        with SessionLocal() as session:
            snapshots = list(
                session.execute(
                    select(PortfolioHistorySnapshot)
                    .where(
                        PortfolioHistorySnapshot.user_id == user_id,
                        PortfolioHistorySnapshot.snapshot_hour >= cutoff,
                    )
                    .order_by(
                        PortfolioHistorySnapshot.snapshot_hour.asc(),
                        PortfolioHistorySnapshot.source_window.asc(),
                    )
                ).scalars()
            )

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
                self.capture_due_snapshots_for_all_users()
            except Exception:
                logger.exception("Portfolio history sampler failed")

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
        except Exception:
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
