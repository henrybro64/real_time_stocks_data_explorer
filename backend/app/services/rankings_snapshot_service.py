"""Periodic background refresh of market-wide rankings quote snapshots.

A CronJob calls ``RankingsSnapshotService.refresh(market)`` every ~30 min
during market hours. The service determines the universe for the market,
fetches a fresh quote for every stock in parallel, and writes the whole set
to Redis as a single snapshot.

The Rankings API then reads from this snapshot rather than fanning out live
quote fetches inside the HTTP request, which makes it feasible to rank a
1000+-stock universe (e.g. TWSE-listed) without timing out the request.

Phase 1 only implements Taiwan, where the universe is reused from the
existing TWSE T86 daily institutional snapshot (zero extra fetches to learn
which stocks to track). U.S. (S&P 500) is a planned Phase 2 follow-up; the
class is structured so that path slots in without churning Phase 1 behavior.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

from app.services.market_clock import is_market_open
from app.services.quote_service import QuoteService, quote_service
from app.services.rankings_service import RankingsService, rankings_service
from app.services.rankings_snapshot_cache import (
    RankingsSnapshot,
    SnapshotEntry,
    set_snapshot,
)

logger = logging.getLogger(__name__)

SnapshotMarket = Literal["TW"]

_DEFAULT_MAX_WORKERS = 8


@dataclass(frozen=True)
class RefreshResult:
    market: str
    fetched_at: datetime
    market_open: bool
    universe_size: int
    quote_count: int
    skipped_count: int


def _default_tw_universe_provider(
    helper: RankingsService, now: datetime
) -> list[str]:
    snapshot = helper.fetch_tw_institutional_snapshot(now)
    if snapshot is None:
        return []
    _ranking_date, rows = snapshot
    return sorted(rows.keys())


class RankingsSnapshotService:
    def __init__(
        self,
        *,
        quote_source: QuoteService | None = None,
        rankings_helper: RankingsService | None = None,
        tw_universe_provider: Callable[[datetime], list[str]] | None = None,
        max_workers: int = _DEFAULT_MAX_WORKERS,
    ) -> None:
        self.quote_source = quote_source or quote_service
        self.rankings_helper = rankings_helper or rankings_service
        self.max_workers = max(1, max_workers)
        if tw_universe_provider is not None:
            self._tw_universe_provider = tw_universe_provider
        else:
            self._tw_universe_provider = lambda now: _default_tw_universe_provider(
                self.rankings_helper, now
            )

    def refresh(
        self,
        market: SnapshotMarket,
        now: datetime | None = None,
    ) -> RefreshResult:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if market == "TW":
            return self._refresh_tw(current)
        raise ValueError(f"Unsupported snapshot market: {market}")

    def _refresh_tw(self, now: datetime) -> RefreshResult:
        universe_ids = self._tw_universe_provider(now)
        market_open = is_market_open("TW", now)

        if not universe_ids:
            logger.warning(
                "TW rankings snapshot refresh skipped: empty universe at %s",
                now.isoformat(),
            )
            snapshot = RankingsSnapshot(
                market="TW",
                fetched_at=now,
                market_open=market_open,
                entries=[],
            )
            set_snapshot(snapshot)
            return RefreshResult(
                market="TW",
                fetched_at=now,
                market_open=market_open,
                universe_size=0,
                quote_count=0,
                skipped_count=0,
            )

        entries = self._fetch_entries_parallel(universe_ids)
        snapshot = RankingsSnapshot(
            market="TW",
            fetched_at=now,
            market_open=market_open,
            entries=entries,
        )
        set_snapshot(snapshot)

        logger.info(
            "TW rankings snapshot refreshed: universe=%d, quotes=%d, skipped=%d, "
            "market_open=%s",
            len(universe_ids),
            len(entries),
            len(universe_ids) - len(entries),
            market_open,
        )
        return RefreshResult(
            market="TW",
            fetched_at=now,
            market_open=market_open,
            universe_size=len(universe_ids),
            quote_count=len(entries),
            skipped_count=len(universe_ids) - len(entries),
        )

    def _fetch_entries_parallel(
        self, stock_ids: list[str]
    ) -> list[SnapshotEntry]:
        results_by_id: dict[str, SnapshotEntry] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_id = {
                pool.submit(self._fetch_entry, stock_id): stock_id
                for stock_id in stock_ids
            }
            for future in as_completed(future_to_id):
                stock_id = future_to_id[future]
                try:
                    entry = future.result()
                except Exception as exc:
                    logger.debug(
                        "Rankings snapshot fetch failed for %s: %s", stock_id, exc
                    )
                    continue
                if entry is not None:
                    results_by_id[stock_id] = entry
        return [results_by_id[sid] for sid in stock_ids if sid in results_by_id]

    def _fetch_entry(self, stock_id: str) -> SnapshotEntry | None:
        stock = self.quote_source.get_stock(stock_id)
        if stock is None:
            return None
        quote = self.quote_source.get_quote(stock_id)
        if quote is None:
            return None
        return SnapshotEntry(stock=stock, quote=quote)


rankings_snapshot_service = RankingsSnapshotService()


__all__ = [
    "RankingsSnapshotService",
    "RefreshResult",
    "SnapshotMarket",
    "rankings_snapshot_service",
]
