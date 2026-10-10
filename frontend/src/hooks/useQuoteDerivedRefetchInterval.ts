"use client";

import { useQuery } from "@tanstack/react-query";
import type { MarketStatus } from "@shared/api";
import { fetchMarkets } from "../services/api/client";
import {
  BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS,
  isAnyTrackedMarketOpen,
  WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS,
} from "../views/home/watchlistQuoteQuery";

/** Poll quote-derived UI no faster than the backend refresher (#270 / #131). */
export const QUOTE_DERIVED_REFETCH_INTERVAL_OPEN_MS = BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS;
export const QUOTE_DERIVED_REFETCH_INTERVAL_CLOSED_MS =
  WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS;

/**
 * Market-aware refetch interval for portfolio, assets, and stock-detail quotes.
 * Open (or pre-open) → refresher open cadence; closed → minutes, not 5 s.
 */
export function useQuoteDerivedRefetchInterval(stockIds: string[]): number {
  const marketsQuery = useQuery<MarketStatus[]>({
    queryKey: ["markets"],
    queryFn: fetchMarkets,
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
  const open = isAnyTrackedMarketOpen(stockIds, marketsQuery.data);
  return open
    ? QUOTE_DERIVED_REFETCH_INTERVAL_OPEN_MS
    : QUOTE_DERIVED_REFETCH_INTERVAL_CLOSED_MS;
}
