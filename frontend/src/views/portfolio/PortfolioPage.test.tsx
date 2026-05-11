import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PortfolioHistoryResponse, PortfolioPosition, PortfolioSummary } from "@shared/api";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { PortfolioPage } from "./PortfolioPage";
import { usePortfolioPositions } from "../../hooks/usePortfolioPositions";
import {
  fetchPortfolioHoldingsHistory,
  fetchPortfolioSummary,
  fetchUsdTwdFxRate,
  searchStocks,
} from "../../services/api/client";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

vi.mock("../../components/chart/HistoricalValueChart", () => ({
  HistoricalValueChart: ({ title }: { title: string }) => <div data-testid="historical-value-chart">{title}</div>,
}));

vi.mock("../../components/search/StockSearch", () => ({
  StockSearch: () => <div data-testid="portfolio-search" />,
}));

vi.mock("../../hooks/usePortfolioPositions", () => ({
  usePortfolioPositions: vi.fn(),
}));

vi.mock("../../services/api/client", () => ({
  fetchPortfolioHoldingsHistory: vi.fn(),
  fetchPortfolioSummary: vi.fn(),
  fetchUsdTwdFxRate: vi.fn(),
  searchStocks: vi.fn(),
}));

function buildPosition({
  id,
  stockId,
  symbol,
  exchange,
  currency,
  nameEn,
  nameZhTw,
  lastPrice,
  marketValue,
}: {
  id: number;
  stockId: string;
  symbol: string;
  exchange: string;
  currency: "USD" | "TWD";
  nameEn: string;
  nameZhTw: string;
  lastPrice: number;
  marketValue: number;
}): PortfolioPosition {
  return {
    id,
    stockId,
    quantity: 2,
    averageCost: lastPrice - 5,
    currency,
    notes: null,
    stock: {
      id: stockId,
      market: currency === "USD" ? "US" : "TW",
      symbol,
      name: nameZhTw,
      exchange,
      currency,
      timezone: currency === "USD" ? "America/New_York" : "Asia/Taipei",
      nameEn,
      nameZhTw,
    },
    quote: {
      id: stockId,
      lastPrice,
      change: 1,
      changePercent: 0.5,
      previousClose: lastPrice - 1,
      open: lastPrice - 0.5,
      high: lastPrice + 1,
      low: lastPrice - 1,
      volume: 10_000,
      timestamp: currency === "USD" ? "2026-04-01T09:30:00-04:00" : "2026-04-01T09:00:00+08:00",
      isMarketOpen: true,
    },
    marketValue,
    unrealizedPl: 10,
    unrealizedPlPercent: 5,
  };
}

const positions: PortfolioPosition[] = [
  buildPosition({
    id: 1,
    stockId: "TW:2330",
    symbol: "2330",
    exchange: "TWSE",
    currency: "TWD",
    nameEn: "TSMC",
    nameZhTw: "台積電",
    lastPrice: 950,
    marketValue: 1_900,
  }),
  buildPosition({
    id: 2,
    stockId: "US:AAPL",
    symbol: "AAPL",
    exchange: "NASDAQ",
    currency: "USD",
    nameEn: "Apple Inc.",
    nameZhTw: "蘋果",
    lastPrice: 187.23,
    marketValue: 374.46,
  }),
];

const portfolioSummary: PortfolioSummary = {
  totalUsd: 374.46,
  totalTwd: 1_900,
  combinedUsd: 433.84,
  combinedTwd: 13_883.12,
  usdTwdRate: 32,
  fxTimestamp: "2026-04-01T01:00:00Z",
  positions: [
    {
      stockId: "TW:2330",
      symbol: "2330",
      currency: "TWD",
      quantity: 2,
      lastPrice: 950,
      marketValue: 1_900,
      timestamp: "2026-04-01T09:00:00+08:00",
    },
    {
      stockId: "US:AAPL",
      symbol: "AAPL",
      currency: "USD",
      quantity: 2,
      lastPrice: 187.23,
      marketValue: 374.46,
      timestamp: "2026-04-01T09:30:00-04:00",
    },
  ],
  skippedStockIds: [],
};

const holdingsHistory: PortfolioHistoryResponse = {
  range: "1M",
  metric: "holdings",
  usd: {
    currency: "USD",
    points: [],
    chartLayout: null,
  },
  twd: {
    currency: "TWD",
    points: [],
    chartLayout: null,
  },
};

function renderPortfolioPage(language: "en" | "zh-TW" = "zh-TW") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <PortfolioPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("PortfolioPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(usePortfolioPositions).mockReturnValue({
      positions,
      ids: positions.map((position) => position.stockId),
      isLoading: false,
      isError: false,
      error: null,
      queryError: null,
      mutationError: null,
      actionError: null,
      isMutating: false,
      add: vi.fn(),
      addByStockId: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
    });
    vi.mocked(fetchPortfolioSummary).mockResolvedValue(portfolioSummary);
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue({
      pair: "USD/TWD",
      rate: 32,
      timestamp: "2026-04-01T01:00:00Z",
    });
    vi.mocked(fetchPortfolioHoldingsHistory).mockResolvedValue(holdingsHistory);
    vi.mocked(searchStocks).mockResolvedValue([]);
  });

  it("shows mixed-currency clarification in Traditional Chinese by default", async () => {
    renderPortfolioPage();

    expect(await screen.findByTestId("portfolio-table-currency-hint")).toHaveTextContent(
      "最新價與持倉市值皆依各檔股票原幣顯示，例如 USD 或 TWD。",
    );
    expect(screen.getByRole("columnheader", { name: "最新價（原幣）" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "持倉市值（原幣）" })).toBeInTheDocument();

    const twRow = screen.getByTestId("portfolio-position-TW:2330");
    const usRow = screen.getByTestId("portfolio-position-US:AAPL");
    expect(within(twRow).getByText("TWSE")).toBeInTheDocument();
    expect(twRow).toHaveTextContent("950.00");
    expect(twRow).toHaveTextContent("1,900.00");
    expect(within(usRow).getByText("NASDAQ")).toBeInTheDocument();
    expect(usRow).toHaveTextContent("187.23");
    expect(usRow).toHaveTextContent("374.46");
  });

  it("shows the updated English currency labels when English is selected", async () => {
    renderPortfolioPage("en");

    expect(await screen.findByTestId("portfolio-table-currency-hint")).toHaveTextContent(
      "Last price and position value are shown in each holding's stock currency, such as USD or TWD.",
    );
    expect(screen.getByRole("columnheader", { name: "Last price (stock currency)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Position value (stock currency)" })).toBeInTheDocument();
    expect(screen.getByText("Apple Inc.")).toBeInTheDocument();
    expect(screen.getByText("TSMC")).toBeInTheDocument();
  });
});
