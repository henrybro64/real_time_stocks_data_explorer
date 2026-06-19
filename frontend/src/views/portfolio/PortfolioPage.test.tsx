import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type {
  PortfolioAnalysisResponse,
  PortfolioHistoryResponse,
  PortfolioPosition,
  PortfolioSummary,
} from "@shared/api";
import { render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { PortfolioPage } from "./PortfolioPage";
import { usePortfolioPositions } from "../../hooks/usePortfolioPositions";
import {
  analyzePortfolio,
  ApiError,
  fetchPortfolioHoldingsHistory,
  fetchPortfolioSummary,
  fetchUsdTwdFxRate,
  searchStocks,
} from "../../services/api/client";
import { PORTFOLIO_ANALYSIS_AI_TEMPORARILY_UNAVAILABLE_WARNING, PORTFOLIO_ANALYSIS_BACKEND_ERRORS } from "./portfolioAnalysisErrors";

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

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    analyzePortfolio: vi.fn(),
    fetchPortfolioHoldingsHistory: vi.fn(),
    fetchPortfolioSummary: vi.fn(),
    fetchUsdTwdFxRate: vi.fn(),
    searchStocks: vi.fn(),
  };
});

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
  combinedGainUsd: 12.34,
  combinedGainTwd: 394.88,
  combinedGainPercent: 5.67,
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

const portfolioAnalysis: PortfolioAnalysisResponse = {
  computedSummary: {
    holdingsCount: 2,
    analyzedHoldingsCount: 2,
    skippedHoldingsCount: 0,
    totalCostBasisUsd: 400,
    totalCostBasisTwd: 12_800,
    totalMarketValueUsd: 433.84,
    totalMarketValueTwd: 13_883.12,
    totalUnrealizedPlUsd: 33.84,
    totalUnrealizedPlTwd: 1_083.12,
    totalUnrealizedPlPercent: 8.46,
    usdTwdRate: 32,
    fxTimestamp: "2026-04-01T01:00:00Z",
    currencyExposure: [
      {
        currency: "USD",
        marketValue: 374.46,
        marketValueUsd: 374.46,
        marketValueTwd: 11_982.72,
        portfolioWeightPercent: 86.31,
      },
      {
        currency: "TWD",
        marketValue: 1_900,
        marketValueUsd: 59.38,
        marketValueTwd: 1_900,
        portfolioWeightPercent: 13.69,
      },
    ],
    largestHoldings: [
      {
        stockId: "US:AAPL",
        symbol: "AAPL",
        name: "Apple Inc.",
        market: "US",
        exchange: "NASDAQ",
        currency: "USD",
        quantity: 2,
        averageCost: 180,
        lastPrice: 187.23,
        costBasis: 360,
        costBasisUsd: 360,
        costBasisTwd: 11_520,
        marketValue: 374.46,
        marketValueUsd: 374.46,
        marketValueTwd: 11_982.72,
        unrealizedPl: 14.46,
        unrealizedPlUsd: 14.46,
        unrealizedPlTwd: 462.72,
        unrealizedPlPercent: 4.02,
        portfolioWeightPercent: 86.31,
        notes: null,
      },
    ],
    holdings: [],
    skippedHoldings: [],
    concentrationRisk: {
      level: "high",
      topHoldingWeightPercent: 86.31,
      top3WeightPercent: 100,
      top5WeightPercent: 100,
    },
  },
  aiReport: "Portfolio Summary\nConcentration Risk\nPossible Actions",
  provider: "openai",
  model: "gpt-test",
  warnings: [],
  disclaimer: "AI-generated analysis, not financial advice.",
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
  const updateMock = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    updateMock.mockReset();

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
      update: updateMock,
      remove: vi.fn(),
    });
    vi.mocked(analyzePortfolio).mockResolvedValue(portfolioAnalysis);
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
    expect(twRow).toHaveTextContent("TWD");
    expect(twRow).toHaveTextContent("950.00");
    expect(twRow).toHaveTextContent("1,900.00");
    expect(within(usRow).getByText("NASDAQ")).toBeInTheDocument();
    expect(usRow).toHaveTextContent("USD");
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
    expect(screen.getByRole("columnheader", { name: "Avg cost (stock currency)" })).toBeInTheDocument();
    expect(screen.getByText("Apple Inc.")).toBeInTheDocument();
    expect(screen.getByText("TSMC")).toBeInTheDocument();
  });

  it("renders backend-provided total gain values from the portfolio summary", async () => {
    renderPortfolioPage("en");

    await waitFor(() => {
      expect(screen.getByTestId("portfolio-combined-gain-usd")).toHaveTextContent(
        "+$12.34 (+5.67%)",
      );
      expect(screen.getByTestId("portfolio-combined-gain-twd")).toHaveTextContent(
        "+$394.88 (+5.67%)",
      );
    });
  });

  it("submits average cost updates from the portfolio table", async () => {
    const user = userEvent.setup();
    renderPortfolioPage("en");

    const averageCostInput = await screen.findByTestId("portfolio-average-cost-input-US:AAPL");
    await user.clear(averageCostInput);
    await user.type(averageCostInput, "180");
    await user.keyboard("{Enter}");

    expect(updateMock).toHaveBeenCalledWith(
      {
        positionId: 2,
        payload: { averageCost: 180 },
      },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onSettled: expect.any(Function),
      }),
    );
  });

  it("shows a loading state while AI portfolio analysis is running", async () => {
    let resolveAnalysis: ((value: PortfolioAnalysisResponse) => void) | null = null;
    vi.mocked(analyzePortfolio).mockReturnValue(
      new Promise<PortfolioAnalysisResponse>((resolve) => {
        resolveAnalysis = resolve;
      }),
    );
    const user = userEvent.setup();
    renderPortfolioPage("en");

    await user.click(await screen.findByTestId("portfolio-ai-analyze-button"));

    expect(await screen.findByText("Analyzing portfolio...")).toBeInTheDocument();
  });

  it("shows the required AI-not-configured message", async () => {
    vi.mocked(analyzePortfolio).mockRejectedValue(
      new ApiError(
        `Request failed: 400 - ${PORTFOLIO_ANALYSIS_BACKEND_ERRORS.AI_NOT_CONFIGURED}`,
        {
          status: 400,
          detail: PORTFOLIO_ANALYSIS_BACKEND_ERRORS.AI_NOT_CONFIGURED,
          kind: "validation",
        },
      ),
    );
    const user = userEvent.setup();
    renderPortfolioPage("en");

    await user.click(await screen.findByTestId("portfolio-ai-analyze-button"));

    expect(await screen.findByTestId("portfolio-ai-not-configured")).toHaveTextContent(
      "AI is not configured. Please set up your AI provider in AI Settings.",
    );
    expect(screen.getByRole("link", { name: "AI Settings" })).toBeInTheDocument();
  });

  it("shows a dedicated message when no holdings can be analyzed", async () => {
    vi.mocked(analyzePortfolio).mockRejectedValue(
      new ApiError(
        `Request failed: 400 - ${PORTFOLIO_ANALYSIS_BACKEND_ERRORS.NO_ANALYZABLE_HOLDINGS}`,
        {
          status: 400,
          detail: PORTFOLIO_ANALYSIS_BACKEND_ERRORS.NO_ANALYZABLE_HOLDINGS,
          kind: "validation",
        },
      ),
    );
    const user = userEvent.setup();
    renderPortfolioPage("en");

    await user.click(await screen.findByTestId("portfolio-ai-analyze-button"));

    expect(await screen.findByTestId("portfolio-ai-no-analyzable")).toHaveTextContent(
      "No holdings could be analyzed right now. Review your positions (metadata, currency, and quotes) and try again.",
    );
  });

  it("shows a dedicated message when the portfolio is empty", async () => {
    vi.mocked(analyzePortfolio).mockRejectedValue(
      new ApiError(`Request failed: 400 - ${PORTFOLIO_ANALYSIS_BACKEND_ERRORS.EMPTY_PORTFOLIO}`, {
        status: 400,
        detail: PORTFOLIO_ANALYSIS_BACKEND_ERRORS.EMPTY_PORTFOLIO,
        kind: "validation",
      }),
    );
    const user = userEvent.setup();
    renderPortfolioPage("en");

    await user.click(await screen.findByTestId("portfolio-ai-analyze-button"));

    expect(await screen.findByTestId("portfolio-ai-empty-error")).toHaveTextContent(
      "Your portfolio has no holdings to analyze.",
    );
  });

  it("renders the backend portfolio analysis response", async () => {
    const user = userEvent.setup();
    renderPortfolioPage("en");

    await user.click(await screen.findByTestId("portfolio-ai-analyze-button"));

    expect(await screen.findByTestId("portfolio-ai-report")).toHaveTextContent(
      "Portfolio Summary",
    );
    expect(screen.getByText("openai")).toBeInTheDocument();
    expect(screen.getByText("gpt-test")).toBeInTheDocument();
    expect(screen.getByText("Total market value (USD)")).toBeInTheDocument();
    expect(screen.getByText("AI-generated analysis, not financial advice.")).toBeInTheDocument();
  });

  it("shows Important notices above the AI section when the provider fails without narrative text", async () => {
    vi.mocked(analyzePortfolio).mockResolvedValue({
      ...portfolioAnalysis,
      aiReport: null,
      warnings: [
        "Skipped holdings from AI analysis because stock metadata or current quotes were unavailable: US:XLE",
        PORTFOLIO_ANALYSIS_AI_TEMPORARILY_UNAVAILABLE_WARNING,
      ],
    });
    const user = userEvent.setup();
    renderPortfolioPage("en");

    await user.click(await screen.findByTestId("portfolio-ai-analyze-button"));

    expect(await screen.findByTestId("portfolio-ai-warnings")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Important notices" })).toBeInTheDocument();
    expect(screen.getByTestId("portfolio-ai-no-report")).toHaveTextContent(
      "The AI provider did not return a narrative",
    );
  });

  it("points users to notices when narrative is missing for other warning reasons", async () => {
    vi.mocked(analyzePortfolio).mockResolvedValue({
      ...portfolioAnalysis,
      aiReport: null,
      warnings: [
        "Skipped holdings from AI analysis because stock metadata or current quotes were unavailable: US:XLE",
      ],
    });
    const user = userEvent.setup();
    renderPortfolioPage("en");

    await user.click(await screen.findByTestId("portfolio-ai-analyze-button"));

    expect(await screen.findByTestId("portfolio-ai-warnings")).toBeInTheDocument();
    expect(screen.getByTestId("portfolio-ai-no-report")).toHaveTextContent(
      "Review Important notices above",
    );
  });
});
