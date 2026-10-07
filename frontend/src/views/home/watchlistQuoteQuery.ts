import type { MarketStatus, QuoteResponse } from "@shared/api";
import type { MarketCode } from "@shared/stock";
import {
  quotesAreWarming,
} from "../../services/market-data/quoteWarming";

export {
  isQuoteWarmingTimedOut,
  quotesAreWarming,
  QUOTE_WARMING_TIMEOUT_MS,
} from "../../services/market-data/quoteWarming";

/**
 * Matches backend `QUOTE_REFRESHER_OPEN_POLL_SECONDS` (default 45).
 * Frontend open polling must not outpace the refresher for owns-upstream queues.
 */
export const BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS = 45_000;

/** Browser-side freshness while a tracked market is open (complements Redis TTL). */
export const WATCHLIST_QUOTE_STALE_TIME_OPEN_MS = 30_000;
/** Longer browser cache when tracked markets are closed and quotes are fresh. */
export const WATCHLIST_QUOTE_STALE_TIME_CLOSED_MS = 120_000;
/** Short staleTime while a cold quote is queued/refreshing so focus/remount can retry soon. */
export const WATCHLIST_QUOTE_STALE_TIME_WARMING_MS = 2_000;
/** Visible-tab polling while any tracked market is open (aligned with refresher). */
export const WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS = BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS;
/** Visible-tab polling when all tracked markets are closed and quotes are fresh. */
export const WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS = 180_000;
/**
 * Short visible-tab retry while a newly tracked quote is still warming
 * (`quote: null` + queued/refreshing). Not used for steady-state polling.
 */
export const WATCHLIST_QUOTE_REFETCH_INTERVAL_WARMING_MS = 5_000;

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
  /** Latest batch response; used to accelerate cadence when Redis marks quotes stale/warming. */
  quotes?: QuoteResponse[] | null;
  /** When true, stop the short warming poll even if some rows still report queued. */
  warmingTimedOut?: boolean;
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
 * True when at least one tracked market should use open polling cadence:
 * session open, or within the hour-before-open window (matches the backend refresher).
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

  const statusByMarket = new Map(markets.map((item) => [item.market, item]));
  let knownTracked = 0;
  for (const market of tracked) {
    if (!KNOWN_MARKETS.has(market)) {
      continue;
    }
    const status = statusByMarket.get(market);
    if (status === undefined) {
      continue;
    }
    knownTracked += 1;
    if (status.isOpen || status.withinHourBeforeOpen) {
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
 * Prefer open cadence when freshness metadata says Redis is already soft-stale.
 * Warming (null + queued/refreshing) is handled separately via a short retry interval.
 */
export function quotesNeedFasterRefresh(quotes?: QuoteResponse[] | null): boolean {
  if (!quotes || quotes.length === 0) {
    return false;
  }
  return quotes.some((item) => item.freshness?.stale === true);
}

export function resolveWatchlistQuoteQueryTiming({
  stockIds,
  markets,
  quotes,
  warmingTimedOut = false,
}: ResolveWatchlistQuoteQueryTimingOptions): WatchlistQuoteQueryTiming {
  if (quotesAreWarming(quotes) && !warmingTimedOut) {
    return {
      staleTime: WATCHLIST_QUOTE_STALE_TIME_WARMING_MS,
      refetchInterval: WATCHLIST_QUOTE_REFETCH_INTERVAL_WARMING_MS,
      refetchIntervalInBackground: false,
      refetchOnMount: true,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
    };
  }

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
