import type { MarketStatus, QuoteResponse } from "@shared/api";
import type { MarketCode } from "@shared/stock";

/**
 * Matches backend `QUOTE_REFRESHER_OPEN_POLL_SECONDS` (default 45).
 * Frontend open polling must not outpace the refresher for owns-upstream queues.
 */
export const BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS = 45_000;

/** Browser-side freshness while a tracked market is open (complements Redis TTL). */
export const WATCHLIST_QUOTE_STALE_TIME_OPEN_MS = 30_000;
/** Longer browser cache when tracked markets are closed and quotes are fresh. */
export const WATCHLIST_QUOTE_STALE_TIME_CLOSED_MS = 120_000;
/** Visible-tab polling while any tracked market is open (aligned with refresher). */
export const WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS = BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS;
/** Visible-tab polling when all tracked markets are closed and quotes are fresh. */
export const WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS = 180_000;

/** Legacy aggressive intervals that must not be used for watchlist quotes. */
export const LEGACY_WATCHLIST_REFETCH_INTERVAL_TW_MS = 2_000;
export const LEGACY_WATCHLIST_REFETCH_INTERVAL_OTHER_MS = 5_000;

export type WatchlistQuoteQueryTiming = {
  staleTime: number;
  refetchInterval: number;
  refetchIntervalInBackground: false;
  refetchOnMount: boolean;
  refetchOnWindowFocus: boolean;
  refetchOnReconnect: boolean;
};

type ResolveWatchlistQuoteQueryTimingOptions = {
  stockIds: string[];
  markets?: MarketStatus[] | null;
  /** Latest batch response; used to accelerate cadence when Redis marks quotes stale. */
  quotes?: QuoteResponse[] | null;
};

const KNOWN_MARKETS = new Set<MarketCode>(["TW", "US"]);

export function marketCodesFromStockIds(stockIds: string[]): MarketCode[] {
  const markets = new Set<MarketCode>();
  for (const stockId of stockIds) {
    const prefix = stockId.split(":", 1)[0];
    if (prefix === "TW" || prefix === "US") {
      markets.add(prefix);
    }
  }
  return [...markets];
}

/**
 * True when at least one market represented in the watchlist is open.
 * When market status is unknown, treat as open so we do not under-poll during sessions.
 */
export function isAnyTrackedMarketOpen(
  stockIds: string[],
  markets?: MarketStatus[] | null,
): boolean {
  const tracked = marketCodesFromStockIds(stockIds);
  if (tracked.length === 0) {
    return false;
  }
  if (!markets || markets.length === 0) {
    return true;
  }

  const statusByMarket = new Map(markets.map((item) => [item.market, item.isOpen]));
  let knownTracked = 0;
  for (const market of tracked) {
    if (!KNOWN_MARKETS.has(market)) {
      continue;
    }
    const isOpen = statusByMarket.get(market);
    if (isOpen === undefined) {
      continue;
    }
    knownTracked += 1;
    if (isOpen) {
      return true;
    }
  }

  // Unknown market status for tracked IDs → prefer open cadence.
  if (knownTracked === 0) {
    return true;
  }
  return false;
}

/**
 * Prefer open cadence when freshness metadata says Redis is already stale or still warming.
 */
export function quotesNeedFasterRefresh(quotes?: QuoteResponse[] | null): boolean {
  if (!quotes || quotes.length === 0) {
    return false;
  }
  return quotes.some((item) => {
    const freshness = item.freshness;
    if (!freshness) {
      return false;
    }
    if (freshness.stale) {
      return true;
    }
    if (
      item.quote == null &&
      (freshness.refreshStatus === "queued" || freshness.refreshStatus === "refreshing")
    ) {
      return true;
    }
    return false;
  });
}

export function resolveWatchlistQuoteQueryTiming({
  stockIds,
  markets,
  quotes,
}: ResolveWatchlistQuoteQueryTimingOptions): WatchlistQuoteQueryTiming {
  const useOpenCadence =
    isAnyTrackedMarketOpen(stockIds, markets) || quotesNeedFasterRefresh(quotes);
  return {
    staleTime: useOpenCadence
      ? WATCHLIST_QUOTE_STALE_TIME_OPEN_MS
      : WATCHLIST_QUOTE_STALE_TIME_CLOSED_MS,
    refetchInterval: useOpenCadence
      ? WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS
      : WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS,
    refetchIntervalInBackground: false,
    // Explicit defaults: refetch only when data is stale (respects staleTime).
    refetchOnMount: true,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  };
}
