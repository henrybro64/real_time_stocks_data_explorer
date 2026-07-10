import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { HistoryResponse, QuoteResponse, TaiwanInstitutionalInvestorsResponse } from "@shared/api";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { StockDetailPage } from "./StockDetailPage";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

vi.mock("../../components/chart/PriceChart", () => ({
  PriceChart: ({
    candles,
    isActive = true,
  }: {
    candles: unknown[];
    isActive?: boolean;
  }) => {
    if (!isActive) {
      return null;
    }
    if (candles.length === 0) {
      return <div data-testid="price-chart-empty">沒有可顯示的圖表資料。</div>;
    }
    return <div data-testid="price-chart-mock">price-chart</div>;
  },
}));

vi.mock("../../components/ai/AiInsightPanel", () => ({
  AiInsightPanel: () => <div data-testid="ai-insight-mock">ai</div>,
}));

vi.mock("../../components/key-stats/KeyStatisticsPanel", () => ({
  KeyStatisticsPanel: () => <div data-testid="key-stats-mock">key-stats</div>,
}));

vi.mock("../../components/news/StockNewsPanel", () => ({
  StockNewsPanel: () => <div data-testid="news-mock">news</div>,
}));

vi.mock("../../components/tw-insights/TwInsightsPanel", () => ({
  TwInsightsPanel: () => <div data-testid="tw-insights-mock">tw-insights</div>,
}));

vi.mock("../../components/us-insights/UsInsightsPanel", () => ({
  UsInsightsPanel: () => <div data-testid="us-insights-mock">us-insights</div>,
}));

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query");
  return {
    ...actual,
    useQuery: (options: { queryKey: unknown[] }) => useQueryMock(options),
  };
});

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
    timestamp: "2026-04-21T11:57:00Z",
    isMarketOpen: true,
  },
};

const sampleTaiwanQuote: QuoteResponse = {
  stock: {
    id: "TW:2330",
    market: "TW",
    symbol: "2330",
    name: "TSMC",
    exchange: "TWSE",
    currency: "TWD",
    timezone: "Asia/Taipei",
    nameEn: "TSMC",
    nameZhTw: "台積電",
  },
  quote: {
    id: "TW:2330",
    lastPrice: 980,
    change: 12,
    changePercent: 1.24,
    previousClose: 968,
    open: 972,
    high: 985,
    low: 970,
    volume: 23_456_789,
    timestamp: "2026-04-21T11:57:00Z",
    isMarketOpen: true,
  },
};

const sampleHistory: HistoryResponse = {
  stock: sampleTaiwanQuote.stock,
  range: "1D",
  candles: [
    {
      time: "2026-04-21T01:00:00Z",
      open: 970,
      high: 985,
      low: 968,
      close: 980,
      volume: 1_000_000,
    },
  ],
  chartLayout: {
    points: [{ x: 50, y: 50 }],
    pointsPolyline: "50,50",
    trendUp: true,
    firstClose: 970,
    lastClose: 980,
    minY: 960,
    maxY: 990,
    yAxisTicks: [960, 975, 990],
    xAxisTickIndices: [0],
  },
};

const sampleInstitutional: TaiwanInstitutionalInvestorsResponse = {
  stockId: "TW:2330",
  symbol: "2330",
  market: "TWSE",
  latestDate: "2026-07-06",
  summary: {
    foreignNetLots: 12345,
    investmentTrustNetLots: 1200,
    dealerNetLots: -800,
    totalNetLots: 12745,
  },
  history: [
    {
      date: "2026-07-04",
      foreignNetLots: 500,
      investmentTrustNetLots: 100,
      dealerNetLots: -50,
      totalNetLots: 550,
    },
    {
      date: "2026-07-05",
      foreignNetLots: -200,
      investmentTrustNetLots: 300,
      dealerNetLots: 100,
      totalNetLots: 200,
    },
    {
      date: "2026-07-06",
      foreignNetLots: 12345,
      investmentTrustNetLots: 1200,
      dealerNetLots: -800,
      totalNetLots: 12745,
    },
  ],
  source: "TWSE T86 daily institutional data",
};

function renderStockDetailPage(stockId = "US%3ANVDA") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <StockDetailPage stockId={stockId} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

function mockQueries(options: {
  quote?: QuoteResponse | undefined;
  quoteError?: boolean;
  quoteLoading?: boolean;
  history?: HistoryResponse | undefined;
  historyError?: boolean;
  historyLoading?: boolean;
  institutional?: TaiwanInstitutionalInvestorsResponse | undefined;
  institutionalError?: boolean;
  institutionalLoading?: boolean;
}) {
  useQueryMock.mockImplementation((queryOptions: { queryKey: unknown[] }) => {
    const key = queryOptions.queryKey[0];
    if (key === "quote") {
      return {
        data: options.quote,
        isError: Boolean(options.quoteError),
        isLoading: Boolean(options.quoteLoading),
        error: options.quoteError ? new Error("quote failed") : null,
      };
    }
    if (key === "history") {
      return {
        data: options.history,
        isError: Boolean(options.historyError),
        isLoading: Boolean(options.historyLoading),
        error: options.historyError ? new Error("history failed") : null,
      };
    }
    if (key === "institutional-investors") {
      return {
        data: "institutional" in options ? options.institutional : sampleInstitutional,
        isError: Boolean(options.institutionalError),
        isLoading: Boolean(options.institutionalLoading),
        error: options.institutionalError ? new Error("institutional failed") : null,
      };
    }
    return {
      data: undefined,
      isError: false,
      isLoading: false,
      error: null,
    };
  });
}

describe("StockDetailPage", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-21T12:00:00Z"));
    useQueryMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows stale notice and stale freshness badge when quote refresh fails with cached data", () => {
    mockQueries({ quote: sampleQuote, quoteError: true });
    renderStockDetailPage();

    expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("過舊");
  });

  it("shows an invalid-id empty state when the route stock id is missing", () => {
    mockQueries({});
    renderStockDetailPage("");

    expect(screen.getByText("找不到股票代碼。")).toBeInTheDocument();
  });

  it("shows a loading state while the quote is loading", () => {
    mockQueries({ quoteLoading: true });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("載入股票明細中")).toBeInTheDocument();
    expect(screen.queryByTestId("quote-summary-section")).not.toBeInTheDocument();
  });

  it("shows an error notice when the quote fails without cached data", () => {
    mockQueries({ quoteError: true });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("載入失敗")).toBeInTheDocument();
    expect(screen.getByText(/TW:2330發生載入問題/)).toBeInTheDocument();
    expect(screen.queryByTestId("quote-summary-section")).not.toBeInTheDocument();
  });

  it("decodes the stock id from the URL and renders symbol metadata", () => {
    mockQueries({ quote: sampleTaiwanQuote, history: sampleHistory });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByTestId("quote-summary-section")).toBeInTheDocument();
    expect(screen.getByText("2330")).toBeInTheDocument();
    expect(screen.getByText("台積電")).toBeInTheDocument();
    expect(screen.getByText("TWSE")).toBeInTheDocument();
  });

  it("renders the price chart when OHLC history is available", () => {
    mockQueries({ quote: sampleTaiwanQuote, history: sampleHistory });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getAllByTestId("price-chart-mock").length).toBeGreaterThan(0);
  });

  it("shows a loading indicator while history is loading", () => {
    mockQueries({ quote: sampleTaiwanQuote, historyLoading: true });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("載入股票明細中")).toBeInTheDocument();
    expect(screen.queryByTestId("price-chart-mock")).not.toBeInTheDocument();
    expect(screen.queryByTestId("stock-detail-chart-empty")).not.toBeInTheDocument();
  });

  it("shows a clear chart empty state when OHLC history is unavailable", () => {
    mockQueries({ quote: sampleTaiwanQuote, history: undefined, historyError: true });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByTestId("stock-detail-chart-empty")).toHaveTextContent("沒有可顯示的圖表資料。");
    expect(screen.queryByTestId("price-chart-mock")).not.toBeInTheDocument();
  });

  it("shows the chart empty state when history returns no candles", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: {
        ...sampleHistory,
        candles: [],
        chartLayout: null,
      },
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getAllByTestId("price-chart-empty").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("price-chart-mock")).not.toBeInTheDocument();
    expect(screen.queryByTestId("stock-detail-chart-empty")).not.toBeInTheDocument();
  });

  it("renders Taiwan institutional summary and charts plus the margin-short placeholder tab", () => {
    mockQueries({ quote: sampleTaiwanQuote, history: sampleHistory });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByTestId("tw-stock-signals-section")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "法人動態" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "資券變化" })).toBeInTheDocument();
    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("三大法人買賣超");
    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("+12,345 張");
    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("+1,200 張");
    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("-800 張");
    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("+12,745 張");
    expect(screen.getByLabelText("外資買賣超(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("投信買賣超(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("自營商買賣超(張)")).toBeInTheDocument();
    expect(screen.getByTestId("tw-stock-signals-margin-short-panel")).toHaveTextContent(/資券變化將顯示於此/);
  });

  it("shows a Taiwan institutional loading state while the section is fetching", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      institutional: undefined,
      institutionalLoading: true,
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("載入法人動態中")).toBeInTheDocument();
  });

  it("shows a Taiwan institutional unavailable message when the API returns no data", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      institutional: {
        ...sampleInstitutional,
        latestDate: null,
        summary: null,
        history: [],
      },
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByTestId("tw-stock-signals-institutional-panel")).toHaveTextContent(
      "目前沒有可顯示的三大法人資料。",
    );
  });

  it("shows a Taiwan institutional error notice when the API fails", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      institutional: undefined,
      institutionalError: true,
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("載入失敗")).toBeInTheDocument();
    expect(screen.getByText(/法人動態發生載入問題/)).toBeInTheDocument();
  });

  it("shows the institutional latestDate in the summary header", () => {
    mockQueries({ quote: sampleTaiwanQuote, history: sampleHistory });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("2026/07/06");
  });

  it("keeps institutional summary visible with a stale notice when refresh fails", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      institutional: sampleInstitutional,
      institutionalError: true,
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("+12,345 張");
  });

  it("does not show Taiwan signal placeholders for US stocks", () => {
    mockQueries({ quote: sampleQuote, history: { ...sampleHistory, stock: sampleQuote.stock } });
    renderStockDetailPage("US%3ANVDA");

    expect(screen.queryByTestId("tw-stock-signals-section")).not.toBeInTheDocument();
    expect(useQueryMock.mock.calls.every((call) => call[0]?.queryKey?.[0] !== "institutional-investors")).toBe(
      true,
    );
  });
});
