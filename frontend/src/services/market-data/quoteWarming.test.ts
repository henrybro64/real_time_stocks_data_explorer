import { describe, expect, it } from "vitest";

import type { QuoteResponse } from "@shared/api";
import {
  isNullQuoteWarming,
  isQuoteWarmingTimedOut,
  quotesAreWarming,
  QUOTE_WARMING_TIMEOUT_MS,
} from "./quoteWarming";

const warmingItem: QuoteResponse = {
  stock: {
    id: "TW:6562",
    market: "TW",
    symbol: "6562",
    name: "聯亞",
    exchange: "TPEx",
    currency: "TWD",
    timezone: "Asia/Taipei",
  },
  quote: null,
  freshness: {
    cachedAt: null,
    ageSeconds: null,
    stale: true,
    source: "none",
    refreshStatus: "queued",
  },
};

describe("quoteWarming", () => {
  it("detects null+queued warming rows", () => {
    expect(isNullQuoteWarming(warmingItem)).toBe(true);
    expect(quotesAreWarming([warmingItem])).toBe(true);
    expect(
      isNullQuoteWarming({
        ...warmingItem,
        quote: {
          id: "TW:6562",
          lastPrice: 1,
          change: 0,
          changePercent: 0,
          previousClose: 1,
          timestamp: "2026-07-13T10:00:00+08:00",
          isMarketOpen: true,
        },
      }),
    ).toBe(false);
  });

  it("bounds warming timeout from started-at timestamps", () => {
    expect(isQuoteWarmingTimedOut(1_000, 1_000 + QUOTE_WARMING_TIMEOUT_MS - 1)).toBe(false);
    expect(isQuoteWarmingTimedOut(1_000, 1_000 + QUOTE_WARMING_TIMEOUT_MS)).toBe(true);
    expect(isQuoteWarmingTimedOut(null, 5_000)).toBe(false);
  });
});
