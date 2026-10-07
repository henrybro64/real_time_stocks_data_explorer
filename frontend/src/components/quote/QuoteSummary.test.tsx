import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Quote } from "@shared/quote";
import type { Stock } from "@shared/stock";

import { LanguageProvider } from "../../app/i18n";
import { formatCurrency } from "../../services/format/quote";
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

  it("shows the corrected Taiwan daily move instead of a false zero change (#258)", () => {
    renderTaiwanSummary({
      lastPrice: 1182,
      change: 77,
      changePercent: 6.97,
      previousClose: 1105,
      open: 1160,
      high: 1215,
      low: 1150,
    });

    expect(screen.getByTestId("quote-summary-price")).toHaveTextContent(formatCurrency(1182, "TWD"));
    const change = screen.getByTestId("quote-summary-change");
    expect(change).toHaveTextContent(`+${formatCurrency(77, "TWD")} (+6.97%)`);
    expect(change).not.toHaveTextContent("0.00%");
  });

  it("still shows 0.00% for a genuinely unchanged Taiwan quote", () => {
    renderTaiwanSummary({ lastPrice: 1105, change: 0, changePercent: 0, previousClose: 1105 });

    expect(screen.getByTestId("quote-summary-change")).toHaveTextContent(
      `+${formatCurrency(0, "TWD")} (+0.00%)`,
    );
  });

  it("does not label a previous-session Taiwan fallback quote as live", () => {
    renderTaiwanSummary({
      lastPrice: 1105,
      change: 33,
      changePercent: 3.08,
      previousClose: 1072,
      timestamp: "2026-04-20T05:30:00Z",
      isMarketOpen: false,
    });

    const tag = screen.getByTestId("quote-freshness-tag");
    expect(tag).toHaveTextContent("已收盤");
    expect(tag).not.toHaveTextContent("即時");
  });

  it("shows a large red limit-up price for a Taiwan quote", () => {
    renderTaiwanSummary({ priceLimitStatus: "limit_up", lastPrice: 26.95, change: 2.45, changePercent: 10 });

    const price = screen.getByTestId("quote-summary-price");
    const accessibleName = `${formatCurrency(26.95, "TWD")}, 漲停`;
    expect(price).toHaveClass("app-price-limit", "app-price-limit--detail", "app-price-limit--up");
    expect(price).not.toHaveClass("app-price-limit--card");
    expect(price).toHaveAttribute("role", "group");
    expect(screen.getByRole("group", { name: accessibleName })).toBe(price);
    expect(screen.getByTestId("quote-summary-price-label")).toHaveTextContent("漲停");
    expect(price).toHaveAttribute("aria-label", accessibleName);
    expect(screen.getByTestId("quote-summary-change")).toHaveTextContent("+10.00%");
    expect(screen.getByTestId("quote-create-alert-button")).toBeInTheDocument();
  });

  it("shows a large green limit-down price for a Taiwan quote", () => {
    renderTaiwanSummary({ priceLimitStatus: "limit_down", lastPrice: 90, change: -10, changePercent: -10 });

    const price = screen.getByTestId("quote-summary-price");
    expect(price).toHaveClass("app-price-limit--detail", "app-price-limit--down");
    expect(screen.getByTestId("quote-summary-price-label")).toHaveTextContent("跌停");
  });

  it("keeps the normal detail price for a Taiwan quote that is not at the limit", () => {
    renderTaiwanSummary({ priceLimitStatus: "none", lastPrice: 110, change: 10, changePercent: 10 });

    const price = screen.getByTestId("quote-summary-price");
    expect(price).toHaveClass("app-detail-price");
    expect(price).not.toHaveClass("app-price-limit");
    expect(screen.queryByTestId("quote-summary-price-label")).not.toBeInTheDocument();
  });

  it("does not invent a limit treatment when Taiwan status is unknown", () => {
    renderTaiwanSummary({ priceLimitStatus: "unknown", changePercent: 10 });

    expect(screen.getByTestId("quote-summary-price")).not.toHaveClass("app-price-limit");
    expect(screen.queryByText("漲停")).not.toBeInTheDocument();
  });

  it("does not restyle a U.S. detail price", () => {
    renderSummary();

    const price = screen.getByTestId("quote-summary-price");
    expect(price).toHaveClass("app-detail-price");
    expect(price).not.toHaveClass("app-price-limit");
    expect(price).toHaveTextContent(formatCurrency(900, "USD"));
  });

  it("uses the English limit-down label on the detail price", () => {
    window.localStorage.setItem("stock-ticker-language", "en");
    renderTaiwanSummary({ priceLimitStatus: "limit_down", lastPrice: 90, change: -10, changePercent: -10 });

    const accessibleName = `${formatCurrency(90, "TWD")}, Limit down`;
    expect(screen.getByTestId("quote-summary-price-label")).toHaveTextContent("Limit down");
    expect(screen.getByRole("group", { name: accessibleName })).toHaveAttribute(
      "aria-label",
      accessibleName,
    );
  });
});

function renderTaiwanSummary(quotePatch: Partial<Quote>) {
  return render(
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
            ...quotePatch,
          },
        }}
      />
    </LanguageProvider>,
  );
}
