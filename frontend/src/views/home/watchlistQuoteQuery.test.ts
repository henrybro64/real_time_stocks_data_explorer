import { describe, expect, it } from "vitest";

import type { MarketStatus, QuoteResponse } from "@shared/api";

import {
  BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS,
  isAnyTrackedMarketOpen,
  LEGACY_WATCHLIST_REFETCH_INTERVAL_OTHER_MS,
  LEGACY_WATCHLIST_REFETCH_INTERVAL_TW_MS,
  marketCodesFromStockIds,
  quotesNeedFasterRefresh,
  resolveWatchlistQuoteQueryTiming,
  WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS,
  WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS,
  WATCHLIST_QUOTE_STALE_TIME_CLOSED_MS,
  WATCHLIST_QUOTE_STALE_TIME_OPEN_MS,
} from "./watchlistQuoteQuery";

const TW_OPEN: MarketStatus = {
  market: "TW",
  label: "Taiwan",
  timezone: "Asia/Taipei",
  isOpen: true,
  localTime: "2026-07-13T10:00:00+08:00",
  withinHourBeforeOpen: false,
};

const TW_CLOSED: MarketStatus = {
  ...TW_OPEN,
  isOpen: false,
  localTime: "2026-07-13T20:00:00+08:00",
};

const US_OPEN: MarketStatus = {
  market: "US",
  label: "U.S.",
  timezone: "America/New_York",
  isOpen: true,
  localTime: "2026-07-13T10:00:00-04:00",
  withinHourBeforeOpen: false,
};

const US_CLOSED: MarketStatus = {
  ...US_OPEN,
  isOpen: false,
  localTime: "2026-07-13T20:00:00-04:00",
};

const freshQuote: QuoteResponse = {
  stock: {
    id: "TW:2330",
    market: "TW",
    symbol: "2330",
    name: "台積電",
    exchange: "TWSE",
    currency: "TWD",
    timezone: "Asia/Taipei",
  },
  quote: {
    id: "TW:2330",
    lastPrice: 1000,
    change: 10,
    changePercent: 1,
    previousClose: 990,
    timestamp: "2026-07-13T10:00:00+08:00",
    isMarketOpen: false,
  },
  freshness: {
    cachedAt: "2026-07-13T10:00:00+08:00",
    ageSeconds: 12,
    stale: false,
    source: "redis",
    refreshStatus: "idle",
  },
};

const staleQuote: QuoteResponse = {
  ...freshQuote,
  freshness: {
    cachedAt: "2026-07-13T09:00:00+08:00",
    ageSeconds: 120,
    stale: true,
    source: "redis",
    refreshStatus: "queued",
  },
};

describe("marketCodesFromStockIds", () => {
  it("extracts unique TW/US markets from stock ids", () => {
    expect(marketCodesFromStockIds(["TW:2330", "US:AAPL", "TW:2317"])).toEqual(["TW", "US"]);
  });
});

describe("isAnyTrackedMarketOpen", () => {
  it("returns true when a tracked market is open", () => {
    expect(isAnyTrackedMarketOpen(["TW:2330"], [TW_OPEN, US_CLOSED])).toBe(true);
  });

  it("returns false when all tracked markets are closed", () => {
    expect(isAnyTrackedMarketOpen(["TW:2330", "US:AAPL"], [TW_CLOSED, US_CLOSED])).toBe(false);
  });

  it("ignores untracked markets that are open", () => {
    expect(isAnyTrackedMarketOpen(["TW:2330"], [TW_CLOSED, US_OPEN])).toBe(false);
  });

  it("defaults to open when market status is unavailable", () => {
    expect(isAnyTrackedMarketOpen(["TW:2330"], null)).toBe(true);
    expect(isAnyTrackedMarketOpen(["TW:2330"], [])).toBe(true);
  });
});

describe("quotesNeedFasterRefresh", () => {
  it("returns false for fresh quotes", () => {
    expect(quotesNeedFasterRefresh([freshQuote])).toBe(false);
  });

  it("returns true when any quote is marked stale", () => {
    expect(quotesNeedFasterRefresh([freshQuote, staleQuote])).toBe(true);
  });

  it("returns true when quote is missing while refresh is in flight", () => {
    expect(
      quotesNeedFasterRefresh([
        {
          ...freshQuote,
          quote: undefined,
          freshness: {
            cachedAt: null,
            ageSeconds: null,
            stale: false,
            source: "none",
            refreshStatus: "refreshing",
          },
        },
      ]),
    ).toBe(true);
  });
});

describe("resolveWatchlistQuoteQueryTiming", () => {
  it("uses non-zero open staleTime and interval aligned with the backend refresher", () => {
    const timing = resolveWatchlistQuoteQueryTiming({
      stockIds: ["TW:2330"],
      markets: [TW_OPEN, US_CLOSED],
    });

    expect(timing.staleTime).toBe(WATCHLIST_QUOTE_STALE_TIME_OPEN_MS);
    expect(timing.staleTime).toBeGreaterThan(0);
    expect(timing.refetchInterval).toBe(WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS);
    expect(timing.refetchInterval).toBe(BACKEND_QUOTE_REFRESHER_OPEN_POLL_MS);
    expect(timing.refetchInterval).toBeGreaterThan(LEGACY_WATCHLIST_REFETCH_INTERVAL_TW_MS);
    expect(timing.refetchInterval).toBeGreaterThan(LEGACY_WATCHLIST_REFETCH_INTERVAL_OTHER_MS);
    expect(timing.refetchIntervalInBackground).toBe(false);
    expect(timing.refetchOnMount).toBe(true);
    expect(timing.refetchOnWindowFocus).toBe(true);
    expect(timing.refetchOnReconnect).toBe(true);
  });

  it("uses slower closed-market staleTime and polling than open-market values", () => {
    const openTiming = resolveWatchlistQuoteQueryTiming({
      stockIds: ["TW:2330"],
      markets: [TW_OPEN],
    });
    const closedTiming = resolveWatchlistQuoteQueryTiming({
      stockIds: ["TW:2330"],
      markets: [TW_CLOSED, US_CLOSED],
      quotes: [freshQuote],
    });

    expect(closedTiming.staleTime).toBe(WATCHLIST_QUOTE_STALE_TIME_CLOSED_MS);
    expect(closedTiming.refetchInterval).toBe(WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS);
    expect(closedTiming.staleTime).toBeGreaterThan(openTiming.staleTime);
    expect(closedTiming.refetchInterval).toBeGreaterThan(openTiming.refetchInterval);
    expect(closedTiming.refetchIntervalInBackground).toBe(false);
  });

  it("uses open cadence when any tracked market is open in a mixed list", () => {
    const timing = resolveWatchlistQuoteQueryTiming({
      stockIds: ["TW:2330", "US:AAPL"],
      markets: [TW_CLOSED, US_OPEN],
    });

    expect(timing.refetchInterval).toBe(WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS);
    expect(timing.staleTime).toBe(WATCHLIST_QUOTE_STALE_TIME_OPEN_MS);
  });

  it("uses open cadence when markets are closed but quote freshness is stale", () => {
    const timing = resolveWatchlistQuoteQueryTiming({
      stockIds: ["TW:2330"],
      markets: [TW_CLOSED, US_CLOSED],
      quotes: [staleQuote],
    });

    expect(timing.refetchInterval).toBe(WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS);
    expect(timing.staleTime).toBe(WATCHLIST_QUOTE_STALE_TIME_OPEN_MS);
  });
});
