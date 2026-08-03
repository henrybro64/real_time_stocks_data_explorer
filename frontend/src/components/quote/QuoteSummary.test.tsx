import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Quote } from "@shared/quote";
import type { Stock } from "@shared/stock";

import { LanguageProvider } from "../../app/i18n";
import { QuoteSummary } from "./QuoteSummary";

const sampleStock: Stock = {
  id: "US:NVDA",
  market: "US",
  symbol: "NVDA",
  name: "NVIDIA Corporation",
  exchange: "NASDAQ",
  currency: "USD",
  timezone: "America/New_York",
  nameEn: "NVIDIA Corporation",
  nameZhTw: null,
};

const sampleQuote: Quote = {
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
};

function renderSummary(refreshFailed = false) {
  return render(
    <LanguageProvider>
      <QuoteSummary item={{ stock: sampleStock, quote: sampleQuote }} refreshFailed={refreshFailed} />
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
    render(
      <LanguageProvider>
        <QuoteSummary
          item={{
            stock: sampleStock,
            quote: { ...sampleQuote, timestamp: "2026-04-21T11:57:00Z" },
          }}
          refreshFailed
        />
      </LanguageProvider>,
    );

    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("過舊");
  });

  it("uses the U.S. price tone convention for positive changes", () => {
    renderSummary();

    const change = screen.getByTestId("quote-summary-change");
    expect(change).toHaveClass("app-price-up", "app-price-convention-us");
    expect(change).toHaveTextContent("+");
    expect(change).not.toHaveClass("app-positive");
  });

  it("uses the Taiwan price tone convention for positive changes", () => {
    render(
      <LanguageProvider>
        <QuoteSummary
          item={{
            stock: {
              id: "TW:2330",
              market: "TW",
              symbol: "2330",
              name: "台積電",
              exchange: "TWSE",
              currency: "TWD",
              timezone: "Asia/Taipei",
              nameEn: "TSMC",
              nameZhTw: "台積電",
            },
            quote: {
              ...sampleQuote,
              id: "TW:2330",
              change: 12,
              changePercent: 1.24,
            },
          }}
        />
      </LanguageProvider>,
    );

    const change = screen.getByTestId("quote-summary-change");
    expect(change).toHaveClass("app-price-up", "app-price-convention-taiwan");
    expect(change).toHaveTextContent("+");
    expect(change).not.toHaveClass("app-positive");
  });

  it("uses the Taiwan price tone convention for negative changes", () => {
    render(
      <LanguageProvider>
        <QuoteSummary
          item={{
            stock: {
              id: "TW:2330",
              market: "TW",
              symbol: "2330",
              name: "台積電",
              exchange: "TWSE",
              currency: "TWD",
              timezone: "Asia/Taipei",
              nameEn: "TSMC",
              nameZhTw: "台積電",
            },
            quote: {
              ...sampleQuote,
              id: "TW:2330",
              change: -8,
              changePercent: -0.82,
            },
          }}
        />
      </LanguageProvider>,
    );

    const change = screen.getByTestId("quote-summary-change");
    expect(change).toHaveClass("app-price-down", "app-price-convention-taiwan");
    expect(change).toHaveTextContent("-");
  });
});
