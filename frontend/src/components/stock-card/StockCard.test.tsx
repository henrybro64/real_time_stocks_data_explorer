import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { fetchQuote } from "../../services/api/client";
import { PRICE_STROKE_COLORS } from "../../services/market-data/priceTone";
import { StockCard } from "./StockCard";

const pushMock = vi.fn();
const fetchHistoryMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>("../../services/api/client");
  return {
    ...actual,
    fetchQuote: vi.fn(),
    fetchHistory: (...args: unknown[]) => fetchHistoryMock(...args),
  };
});

const trendingUpLayout = {
  minY: 90,
  maxY: 110,
  pointsPolyline: "0,80 50,40 100,20",
  points: [
    { x: 0, y: 80 },
    { x: 50, y: 40 },
    { x: 100, y: 20 },
  ],
  yAxisTicks: [110, 100, 90],
  xAxisTickIndices: [0, 1, 2],
  firstClose: 92,
  lastClose: 108,
  trendUp: true,
};

const stock = {
  id: "TW:2330",
  market: "TW" as const,
  symbol: "2330",
  name: "台積電",
  exchange: "TWSE",
  currency: "TWD" as const,
  timezone: "Asia/Taipei",
  nameEn: "TSMC",
  nameZhTw: "台積電",
};

const quote = {
  id: "TW:2330",
  lastPrice: 950,
  change: 5,
  changePercent: 0.53,
  previousClose: 945,
  open: 946,
  high: 952,
  low: 944,
  volume: 1_000_000,
  timestamp: "2026-05-11T09:00:00+08:00",
  isMarketOpen: true,
};

function renderCard(onToggle = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return {
    onToggle,
    ...render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard isTracked={false} onToggle={onToggle} quote={quote} stock={stock} />
        </LanguageProvider>
      </QueryClientProvider>,
    ),
  };
}

describe("StockCard", () => {
  beforeEach(() => {
    window.localStorage.clear();
    pushMock.mockReset();
    fetchHistoryMock.mockReset();
    vi.mocked(fetchQuote).mockReset();
    fetchHistoryMock.mockResolvedValue({
      stock,
      range: "1W",
      candles: [],
      chartLayout: null,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses calm history polling and disables background refetch", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard isTracked={false} onToggle={vi.fn()} quote={quote} stock={stock} />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(fetchHistoryMock).toHaveBeenCalled();
    });

    const historyQuery = queryClient.getQueryCache().find({
      queryKey: ["card-history", stock.id, "1W"],
    });
    const options = historyQuery?.observers[0]?.options;
    expect(options?.staleTime).toBe(120_000);
    expect(options?.refetchInterval).toBe(180_000);
    expect(options?.refetchIntervalInBackground).toBe(false);
  });

  it("navigates to stock detail when the card body is clicked", async () => {
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByTestId("stock-card-TW:2330"));

    expect(pushMock).toHaveBeenCalledWith("/stock/TW%3A2330");
  });

  it("navigates to stock detail when Enter or Space is pressed on the focused card", async () => {
    const user = userEvent.setup();
    renderCard();
    const card = screen.getByTestId("stock-card-TW:2330");

    card.focus();
    await user.keyboard("{Enter}");
    expect(pushMock).toHaveBeenCalledWith("/stock/TW%3A2330");

    pushMock.mockClear();
    card.focus();
    await user.keyboard(" ");
    expect(pushMock).toHaveBeenCalledWith("/stock/TW%3A2330");
  });

  it("does not navigate when an action button is clicked", async () => {
    const user = userEvent.setup();
    const { onToggle } = renderCard();

    await user.click(screen.getByTestId("stock-card-toggle-TW:2330"));

    expect(onToggle).toHaveBeenCalledWith("TW:2330");
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("exposes a direct view-details link to the bookmarkable stock route", () => {
    renderCard();

    expect(screen.getByTestId("stock-card-view-TW:2330")).toHaveAttribute("href", "/stock/TW%3A2330");
  });

  it("uses Taiwan price tone classes for positive quote changes", () => {
    renderCard();

    const change = screen.getByTestId("stock-card-change-TW:2330");
    expect(change).toHaveClass("app-price-up", "app-price-convention-taiwan");
    expect(change).toHaveTextContent("+");
    expect(change).not.toHaveClass("app-positive");
  });

  it("uses U.S. price tone classes for positive quote changes", () => {
    const usStock = {
      id: "US:AAPL",
      market: "US" as const,
      symbol: "AAPL",
      name: "Apple Inc.",
      exchange: "NASDAQ",
      currency: "USD" as const,
      timezone: "America/New_York",
      nameEn: "Apple Inc.",
      nameZhTw: null,
    };
    const usQuote = {
      ...quote,
      id: "US:AAPL",
      change: 1.25,
      changePercent: 0.7,
    };
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard isTracked={false} onToggle={vi.fn()} quote={usQuote} stock={usStock} />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    const change = screen.getByTestId("stock-card-change-US:AAPL");
    expect(change).toHaveClass("app-price-up", "app-price-convention-us");
    expect(change).toHaveTextContent("+");
  });

  it("uses Taiwan sparkline stroke color when the week trend is up", async () => {
    fetchHistoryMock.mockResolvedValue({
      stock,
      range: "1W",
      candles: [
        {
          time: "2026-05-05T13:30:00+08:00",
          open: 90,
          high: 95,
          low: 89,
          close: 92,
          volume: 1000,
        },
        {
          time: "2026-05-06T13:30:00+08:00",
          open: 100,
          high: 110,
          low: 99,
          close: 108,
          volume: 1200,
        },
      ],
      chartLayout: trendingUpLayout,
    });

    renderCard();

    const stroke = await screen.findByTestId("stock-card-sparkline-stroke-TW");
    expect(stroke).toHaveAttribute("stroke", PRICE_STROKE_COLORS.taiwan.up);
  });

  it("uses U.S. sparkline stroke color when the week trend is up", async () => {
    const usStock = {
      id: "US:AAPL",
      market: "US" as const,
      symbol: "AAPL",
      name: "Apple Inc.",
      exchange: "NASDAQ",
      currency: "USD" as const,
      timezone: "America/New_York",
      nameEn: "Apple Inc.",
      nameZhTw: null,
    };
    const usQuote = {
      ...quote,
      id: "US:AAPL",
      change: 1.25,
      changePercent: 0.7,
    };
    fetchHistoryMock.mockResolvedValue({
      stock: usStock,
      range: "1W",
      candles: [
        {
          time: "2026-05-05T16:00:00-04:00",
          open: 170,
          high: 175,
          low: 169,
          close: 172,
          volume: 1000,
        },
        {
          time: "2026-05-06T16:00:00-04:00",
          open: 180,
          high: 190,
          low: 179,
          close: 188,
          volume: 1200,
        },
      ],
      chartLayout: trendingUpLayout,
    });

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard isTracked={false} onToggle={vi.fn()} quote={usQuote} stock={usStock} />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    const stroke = await screen.findByTestId("stock-card-sparkline-stroke-US");
    expect(stroke).toHaveAttribute("stroke", PRICE_STROKE_COLORS.us.up);
    await waitFor(() => {
      expect(fetchHistoryMock).toHaveBeenCalledWith("US:AAPL", "1W");
    });
  });

  it("shows warming state for a null quote that is still queued", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={{
              cachedAt: null,
              ageSeconds: null,
              stale: true,
              source: "none",
              refreshStatus: "queued",
            }}
            isTracked
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("stock-card-quote-refreshing-2330")).toBeInTheDocument();
    expect(screen.getByText("更新報價中")).toBeInTheDocument();
  });

  it("shows unavailable UI immediately when refreshStatus is unavailable", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={{
              cachedAt: null,
              ageSeconds: null,
              stale: true,
              source: "none",
              refreshStatus: "unavailable",
            }}
            isTracked
            onRetryQuote={vi.fn()}
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("stock-card-quote-unavailable-2330")).toBeInTheDocument();
    expect(screen.getByText("目前無法載入報價")).toBeInTheDocument();
    expect(screen.getByText("外部資料來源暫時無法使用，請稍後再試。")).toBeInTheDocument();
    expect(screen.queryByText("更新報價中")).not.toBeInTheDocument();
  });

  it("shows unavailable UI when the parent marks warming as timed out", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const freshness = {
      cachedAt: null,
      ageSeconds: null,
      stale: true,
      source: "none" as const,
      refreshStatus: "queued" as const,
    };
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={freshness}
            isTracked
            onRetryQuote={vi.fn()}
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
            warmingTimedOut={false}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("stock-card-quote-refreshing-2330")).toBeInTheDocument();
    rerender(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={freshness}
            isTracked
            onRetryQuote={vi.fn()}
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
            warmingTimedOut
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("stock-card-quote-unavailable-2330")).toBeInTheDocument();
    expect(screen.queryByTestId("stock-card-quote-refreshing-2330")).not.toBeInTheDocument();
  });

  it("does not call fetchQuote when batch supplies quote null", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={{
              cachedAt: null,
              ageSeconds: null,
              stale: true,
              source: "none",
              refreshStatus: "queued",
            }}
            isTracked
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(fetchHistoryMock).toHaveBeenCalled();
    });
    expect(fetchQuote).not.toHaveBeenCalled();
  });

  it("retries through the parent callback without navigating", async () => {
    const user = userEvent.setup();
    const onRetryQuote = vi.fn();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={{
              cachedAt: null,
              ageSeconds: null,
              stale: true,
              source: "none",
              refreshStatus: "unavailable",
            }}
            isTracked
            onRetryQuote={onRetryQuote}
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    await user.click(screen.getByTestId("stock-card-quote-retry-2330"));
    expect(onRetryQuote).toHaveBeenCalledTimes(1);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("disables retry and shows progress while isRetryingQuote is true", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={{
              cachedAt: null,
              ageSeconds: null,
              stale: true,
              source: "none",
              refreshStatus: "unavailable",
            }}
            isRetryingQuote
            isTracked
            onRetryQuote={vi.fn()}
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("stock-card-quote-retry-2330")).toBeDisabled();
    expect(screen.getByText("重新整理")).toBeInTheDocument();
  });

  it("keeps a stale quote visible when refreshStatus is unavailable", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={{
              cachedAt: "2026-05-11T08:00:00+08:00",
              ageSeconds: 120,
              stale: true,
              source: "redis",
              refreshStatus: "unavailable",
            }}
            isTracked
            onRetryQuote={vi.fn()}
            onToggle={vi.fn()}
            quote={quote}
            stock={stock}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText("$950.00")).toBeInTheDocument();
    expect(screen.queryByTestId("stock-card-quote-unavailable-2330")).not.toBeInTheDocument();
    expect(screen.queryByTestId("stock-card-quote-retry-2330")).not.toBeInTheDocument();
  });

  it("renders English unavailable copy", () => {
    window.localStorage.setItem("stock-ticker-language", "en");
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <StockCard
            freshness={{
              cachedAt: null,
              ageSeconds: null,
              stale: true,
              source: "none",
              refreshStatus: "unavailable",
            }}
            isTracked
            onRetryQuote={vi.fn()}
            onToggle={vi.fn()}
            quote={null}
            stock={stock}
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText("Unable to load quote")).toBeInTheDocument();
    expect(
      screen.getByText("The external data source is temporarily unavailable. Please try again later."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });
});

