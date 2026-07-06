import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuoteResponse } from "@shared/api";

import { LanguageProvider } from "../../app/i18n";
import { QuoteSummary } from "./QuoteSummary";

const sampleQuote: QuoteResponse = {
  stock: {
    id: "US:NVDA",
    market: "US",
    symbol: "NVDA",
    name: "NVIDIA Corporation",
    exchange: "NASDAQ",
    currency: "USD",
    timezone: "America/New_York",
    nameEn: "NVIDIA Corporation",
    nameZhTw: null,
  },
  quote: {
    id: "US:NVDA",
    lastPrice: 900,
    change: 10,
    changePercent: 1.12,
    previousClose: 890,
    open: 892,
    high: 905,
    low: 888,
    volume: 12_345_678,
    timestamp: "2026-04-21T11:59:00Z",
    isMarketOpen: true,
  },
};

function renderSummary(refreshFailed = false) {
  return render(
    <LanguageProvider>
      <QuoteSummary item={sampleQuote} refreshFailed={refreshFailed} />
    </LanguageProvider>,
  );
}

describe("QuoteSummary", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-21T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows an updated-at timestamp and freshness badge", () => {
    renderSummary();

    expect(screen.getByTestId("quote-summary-section")).toBeInTheDocument();
    expect(screen.getByText(/更新時間/)).toBeInTheDocument();
    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("即時");
  });

  it("marks the freshness badge stale when a refresh failed", () => {
    const staleRefreshQuote: QuoteResponse = {
      ...sampleQuote,
      quote: {
        ...sampleQuote.quote,
        timestamp: "2026-04-21T11:57:00Z",
      },
    };

    render(
      <LanguageProvider>
        <QuoteSummary item={staleRefreshQuote} refreshFailed />
      </LanguageProvider>,
    );

    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("過舊");
  });
});
