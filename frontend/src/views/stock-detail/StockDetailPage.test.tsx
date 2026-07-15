import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type {
  HistoryResponse,
  QuoteResponse,
  TaiwanInstitutionalInvestorsResponse,
  TaiwanMarginShortResponse,
} from "@shared/api";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError } from "../../services/api/client";
import { StockDetailPage } from "./StockDetailPage";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

vi.mock("../../components/chart/PriceChart", () => ({
  PriceChart: ({
    candles,
    isActive = true,
    range,
  }: {
    candles: unknown[];
    isActive?: boolean;
    range: string;
  }) => {
    if (!isActive) {
      return null;
    }
    if (candles.length === 0) {
      return <div data-testid="price-chart-empty">沒有可顯示的圖表資料。</div>;
    }
    return (
      <div data-testid="price-chart-mock" data-range={range} data-candle-count={candles.length}>
        price-chart:{range}:{candles.length}
      </div>
    );
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
  sharesPerLot: 1000,
  source: "TWSE T86 daily institutional data",
};

const sampleMarginShort: TaiwanMarginShortResponse = {
  stockId: "TW:2330",
  symbol: "2330",
  market: "TWSE",
  latestDate: "2026-07-06",
  summary: {
    marginBalanceLots: 31339,
    shortMarginBalanceLots: 0,
    borrowedShortBalanceLots: 12345,
    borrowedShortBalanceChangeLots: 141,
    borrowedShortBalanceChangeAmount10k: 2609,
  },
  history: [
    {
      date: "2026-07-04",
      closePrice: 181,
      marginBalanceLots: 31000,
      shortMarginBalanceLots: 200,
      borrowedShortBalanceLots: 12100,
      borrowedShortBalanceChangeLots: -20,
      borrowedShortBalanceChangeAmount10k: -362,
    },
    {
      date: "2026-07-05",
      closePrice: 183,
      marginBalanceLots: 31220,
      shortMarginBalanceLots: 120,
      borrowedShortBalanceLots: 12204,
      borrowedShortBalanceChangeLots: 104,
      borrowedShortBalanceChangeAmount10k: 1903,
    },
    {
      date: "2026-07-06",
      closePrice: 185,
      marginBalanceLots: 31339,
      shortMarginBalanceLots: 0,
      borrowedShortBalanceLots: 12345,
      borrowedShortBalanceChangeLots: 141,
      borrowedShortBalanceChangeAmount10k: 2609,
    },
  ],
  sharesPerLot: 1000,
  source: "TWSE official margin and borrowed-short daily data",
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
  historyError?: boolean | ApiError;
  historyLoading?: boolean;
  institutional?: TaiwanInstitutionalInvestorsResponse | undefined;
  institutionalError?: boolean;
  institutionalLoading?: boolean;
  marginShort?: TaiwanMarginShortResponse | undefined;
  marginShortError?: boolean;
  marginShortLoading?: boolean;
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
      const historyError =
        options.historyError instanceof Error
          ? options.historyError
          : options.historyError
            ? new Error("history failed")
            : null;
      return {
        data: options.history,
        isError: Boolean(historyError),
        isLoading: Boolean(options.historyLoading),
        error: historyError,
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
    if (key === "margin-short") {
      return {
        data: "marginShort" in options ? options.marginShort : sampleMarginShort,
        isError: Boolean(options.marginShortError),
        isLoading: Boolean(options.marginShortLoading),
        error: options.marginShortError ? new Error("margin-short failed") : null,
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

  it("shows a provider unavailable notice when history fails without cached data", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: undefined,
      historyError: new ApiError("Taiwan daily history temporarily unavailable for TW:6488 (TPEx)", {
        kind: "unavailable",
        status: 502,
        detail: "Taiwan daily history temporarily unavailable for TW:6488 (TPEx)",
      }),
    });
    renderStockDetailPage("TW%3A6488");

    expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
    expect(screen.queryByTestId("stock-detail-chart-empty")).not.toBeInTheDocument();
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

  it("switches history query keys per range and does not keep stale candles", () => {
    const tpexQuote: QuoteResponse = {
      ...sampleTaiwanQuote,
      stock: {
        ...sampleTaiwanQuote.stock,
        id: "TW:6488",
        symbol: "6488",
        name: "環球晶",
        exchange: "TPEx",
        nameEn: "GlobalWafers",
        nameZhTw: "環球晶",
      },
      quote: {
        ...sampleTaiwanQuote.quote,
        id: "TW:6488",
      },
    };
    const historyByRange: Record<string, HistoryResponse> = {
      "1D": {
        ...sampleHistory,
        stock: tpexQuote.stock,
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
      },
      "1W": {
        ...sampleHistory,
        stock: tpexQuote.stock,
        range: "1W",
        candles: [
          {
            time: "2026-04-14T05:30:00Z",
            open: 900,
            high: 910,
            low: 890,
            close: 905,
            volume: 2_000_000,
          },
          {
            time: "2026-04-15T05:30:00Z",
            open: 905,
            high: 920,
            low: 900,
            close: 915,
            volume: 2_100_000,
          },
        ],
      },
      "1M": {
        ...sampleHistory,
        stock: tpexQuote.stock,
        range: "1M",
        candles: [
          {
            time: "2026-03-21T05:30:00Z",
            open: 850,
            high: 860,
            low: 840,
            close: 855,
            volume: 3_000_000,
          },
          {
            time: "2026-03-28T05:30:00Z",
            open: 855,
            high: 870,
            low: 850,
            close: 865,
            volume: 3_100_000,
          },
          {
            time: "2026-04-10T05:30:00Z",
            open: 865,
            high: 880,
            low: 860,
            close: 875,
            volume: 3_200_000,
          },
        ],
      },
    };

    useQueryMock.mockImplementation((queryOptions: { queryKey: unknown[] }) => {
      const key = queryOptions.queryKey[0];
      if (key === "quote") {
        return { data: tpexQuote, isError: false, isLoading: false, error: null };
      }
      if (key === "history") {
        const range = String(queryOptions.queryKey[2] ?? "1D");
        return {
          data: historyByRange[range] ?? historyByRange["1D"],
          isError: false,
          isLoading: false,
          error: null,
        };
      }
      return { data: undefined, isError: false, isLoading: false, error: null };
    });

    renderStockDetailPage("TW%3A6488");

    expect(screen.getByTestId("price-chart-mock")).toHaveAttribute("data-range", "1D");
    expect(screen.getByTestId("price-chart-mock")).toHaveAttribute("data-candle-count", "1");

    fireEvent.click(screen.getByRole("tab", { name: "1W" }));
    expect(screen.getByRole("tab", { name: "1W", selected: true })).toBeInTheDocument();
    expect(screen.getByTestId("price-chart-mock")).toHaveAttribute("data-range", "1W");
    expect(screen.getByTestId("price-chart-mock")).toHaveAttribute("data-candle-count", "2");
    expect(
      useQueryMock.mock.calls.some(
        (call) =>
          Array.isArray(call[0]?.queryKey) &&
          call[0].queryKey[0] === "history" &&
          call[0].queryKey[1] === "TW:6488" &&
          call[0].queryKey[2] === "1W",
      ),
    ).toBe(true);

    fireEvent.click(screen.getByRole("tab", { name: "1M" }));
    expect(screen.getByTestId("price-chart-mock")).toHaveAttribute("data-range", "1M");
    expect(screen.getByTestId("price-chart-mock")).toHaveAttribute("data-candle-count", "3");
    expect(screen.queryByText("price-chart:1W:2")).not.toBeInTheDocument();
  });

  it("keeps all declared history range tabs available for chart selection", () => {
    mockQueries({ quote: sampleTaiwanQuote, history: sampleHistory });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByRole("tab", { name: "1D", selected: true })).toBeInTheDocument();
    for (const range of ["1W", "1M", "3M", "6M", "1Y"] as const) {
      expect(screen.getByRole("tab", { name: range })).toBeInTheDocument();
    }
  });

  it("renders Taiwan institutional and margin-short summaries plus the chart tabs", () => {
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
    expect(screen.getByTestId("tw-margin-short-summary")).toHaveTextContent("資券變化");
    expect(screen.getByTestId("tw-margin-short-summary")).toHaveTextContent("31,339 張");
    expect(screen.getByTestId("tw-margin-short-summary")).toHaveTextContent("0 張");
    expect(screen.getByTestId("tw-margin-short-summary")).toHaveTextContent("+141 張");
    expect(screen.getByTestId("tw-margin-short-summary")).toHaveTextContent("+2,609 萬");
    expect(screen.getByLabelText("融資餘額(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("融券餘額(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("借券賣出餘額增減張數")).toBeInTheDocument();
    expect(screen.getByLabelText("借券賣出餘額增減金額(萬)")).toBeInTheDocument();
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

  it("shows a Taiwan margin-short loading state while the section is fetching", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      marginShort: undefined,
      marginShortLoading: true,
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("載入資券變化中")).toBeInTheDocument();
  });

  it("shows a Taiwan margin-short empty state when the API returns no data", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      marginShort: {
        ...sampleMarginShort,
        latestDate: null,
        summary: null,
        history: [],
      },
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByTestId("tw-stock-signals-margin-short-panel")).toHaveTextContent(
      "目前沒有可顯示的資券資料。",
    );
  });

  it("shows a Taiwan margin-short error notice when the API fails", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      marginShort: undefined,
      marginShortError: true,
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("載入失敗")).toBeInTheDocument();
    expect(screen.getByText(/資券變化發生載入問題/)).toBeInTheDocument();
  });

  it("keeps margin-short summary visible with a stale notice when refresh fails", () => {
    mockQueries({
      quote: sampleTaiwanQuote,
      history: sampleHistory,
      marginShort: sampleMarginShort,
      marginShortError: true,
    });
    renderStockDetailPage("TW%3A2330");

    expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    expect(screen.getByTestId("tw-margin-short-summary")).toHaveTextContent("31,339 張");
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
    expect(
      useQueryMock.mock.calls.every((call) => call[0]?.queryKey?.[0] !== "institutional-investors"),
    ).toBe(true);
    expect(
      useQueryMock.mock.calls.every((call) => call[0]?.queryKey?.[0] !== "margin-short"),
    ).toBe(true);
  });
});
