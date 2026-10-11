import {
  focusManager,
  keepPreviousData,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import type { MarketStatus, QuoteResponse } from "@shared/api";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { useWatchlist } from "../../hooks/useWatchlist";
import {
  fetchMarketIndices,
  fetchMarkets,
  fetchQuotes,
  searchStocks,
} from "../../services/api/client";
import { HomePage } from "./HomePage";
import {
  WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS,
  WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS,
  WATCHLIST_QUOTE_STALE_TIME_CLOSED_MS,
  WATCHLIST_QUOTE_STALE_TIME_OPEN_MS,
} from "./watchlistQuoteQuery";

vi.mock("../../hooks/useWatchlist", () => ({
  useWatchlist: vi.fn(),
}));

vi.mock("../../components/search/StockSearch", () => ({
  StockSearch: () => <div data-testid="stock-search" />,
}));

vi.mock("../../components/market-status/MarketStatusStrip", () => ({
  MarketStatusStrip: () => <div data-testid="market-status-strip" />,
}));

vi.mock("../../components/market-status/MarketIndicesStrip", () => ({
  MarketIndicesStrip: () => <div data-testid="market-indices-strip" />,
}));

vi.mock("../../components/market-snapshot/MarketSnapshot", () => ({
  MarketSnapshot: () => <div data-testid="market-snapshot-section" />,
}));

vi.mock("../../components/watchlist/WatchlistTable", () => ({
  WatchlistTable: ({
    isRefreshing,
    isRetryingQuote,
    items,
    onRetryQuote,
  }: {
    isRefreshing?: boolean;
    isRetryingQuote?: boolean;
    items: Array<{ stock: { id: string } }>;
    onRetryQuote?: () => void;
  }) => (
    <div data-testid="watchlist-section">
      {isRefreshing ? <span data-testid="watchlist-refreshing">refreshing</span> : null}
      {isRetryingQuote ? <span data-testid="watchlist-retrying-quote">retrying</span> : null}
      {onRetryQuote ? (
        <button data-testid="watchlist-retry-quote" type="button" onClick={onRetryQuote}>
          retry
        </button>
      ) : null}
      {items.map((item) => (
        <div key={item.stock.id} data-testid={`stock-card-${item.stock.id}`} />
      ))}
    </div>
  ),
}));

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchMarkets: vi.fn(),
    fetchMarketIndices: vi.fn(),
    fetchQuotes: vi.fn(),
    searchStocks: vi.fn(),
  };
});

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

const US_CLOSED: MarketStatus = {
  market: "US",
  label: "U.S.",
  timezone: "America/New_York",
  isOpen: false,
  localTime: "2026-07-13T08:00:00-04:00",
  withinHourBeforeOpen: false,
};

const quoteResponse: QuoteResponse = {
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
    open: 995,
    high: 1005,
    low: 990,
    volume: 1_000_000,
    timestamp: "2026-07-13T10:00:00+08:00",
    isMarketOpen: true,
  },
  freshness: {
    cachedAt: "2026-07-13T10:00:00+08:00",
    ageSeconds: 5,
    stale: false,
    source: "redis",
    refreshStatus: "idle",
  },
};

const secondQuoteResponse: QuoteResponse = {
  stock: {
    id: "TW:2317",
    market: "TW",
    symbol: "2317",
    name: "鴻海",
    exchange: "TWSE",
    currency: "TWD",
    timezone: "Asia/Taipei",
  },
  quote: {
    id: "TW:2317",
    lastPrice: 200,
    change: 1,
    changePercent: 0.5,
    previousClose: 199,
    timestamp: "2026-07-13T10:00:00+08:00",
    isMarketOpen: true,
  },
  freshness: {
    cachedAt: "2026-07-13T10:00:00+08:00",
    ageSeconds: 5,
    stale: false,
    source: "redis",
    refreshStatus: "idle",
  },
};

function renderHome(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <HomePage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

function watchlistQuoteObserverOptions(queryClient: QueryClient, ids: string[] = ["TW:2330"]) {
  const query = queryClient.getQueryCache().find({
    queryKey: ["watchlist-quotes", ids],
  });
  return query?.observers[0]?.options;
}

function resolveOptionValue<T>(
  value: T | ((query: never) => T) | undefined,
  queryClient: QueryClient,
  ids: string[] = ["TW:2330"],
): T | undefined {
  if (typeof value !== "function") {
    return value;
  }
  const query = queryClient.getQueryCache().find({
    queryKey: ["watchlist-quotes", ids],
  });
  if (!query) {
    return undefined;
  }
  return (value as (query: typeof query) => T)(query);
}

describe("HomePage watchlist quote query", () => {
  afterEach(() => {
    vi.useRealTimers();
    focusManager.setFocused(true);
  });

  beforeEach(() => {
    vi.clearAllMocks();
    focusManager.setFocused(true);
    vi.mocked(useWatchlist).mockReturnValue({
      ids: ["TW:2330"],
      hasLoaded: true,
      isLoading: false,
      isError: false,
      isMutating: false,
      error: null,
      mutationError: null,
      queryError: null,
      toggle: vi.fn(),
      remove: vi.fn(),
    });
    vi.mocked(fetchMarkets).mockResolvedValue([TW_OPEN, US_CLOSED]);
    vi.mocked(fetchMarketIndices).mockResolvedValue([]);
    vi.mocked(fetchQuotes).mockResolvedValue([quoteResponse]);
    vi.mocked(searchStocks).mockResolvedValue([]);
  });

  it("configures non-zero staleTime, open-market interval, and disables background polling", async () => {
    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(fetchQuotes).toHaveBeenCalledWith(["TW:2330"]);
    });
    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });

    const options = watchlistQuoteObserverOptions(queryClient);
    expect(resolveOptionValue(options?.staleTime, queryClient)).toBe(
      WATCHLIST_QUOTE_STALE_TIME_OPEN_MS,
    );
    expect(resolveOptionValue(options?.staleTime, queryClient)).toBeGreaterThan(0);
    expect(resolveOptionValue(options?.refetchInterval, queryClient)).toBe(
      WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS,
    );
    expect(options?.refetchIntervalInBackground).toBe(false);
    expect(options?.refetchOnMount).toBe(true);
    expect(options?.refetchOnWindowFocus).toBe(true);
    expect(options?.refetchOnReconnect).toBe(true);
    expect(options?.placeholderData).toBe(keepPreviousData);
  });

  it("uses slower closed-market timing when tracked markets are closed", async () => {
    vi.mocked(fetchMarkets).mockResolvedValue([TW_CLOSED, US_CLOSED]);
    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(fetchQuotes).toHaveBeenCalled();
    });
    await waitFor(() => {
      const options = watchlistQuoteObserverOptions(queryClient);
      expect(resolveOptionValue(options?.staleTime, queryClient)).toBe(
        WATCHLIST_QUOTE_STALE_TIME_CLOSED_MS,
      );
      expect(resolveOptionValue(options?.refetchInterval, queryClient)).toBe(
        WATCHLIST_QUOTE_REFETCH_INTERVAL_CLOSED_MS,
      );
      expect(options?.refetchIntervalInBackground).toBe(false);
    });
  });

  it("uses open cadence when markets are closed but freshness marks quotes stale", async () => {
    vi.mocked(fetchMarkets).mockResolvedValue([TW_CLOSED, US_CLOSED]);
    vi.mocked(fetchQuotes).mockResolvedValue([
      {
        ...quoteResponse,
        quote: { ...quoteResponse.quote!, isMarketOpen: false },
        freshness: {
          cachedAt: "2026-07-13T09:00:00+08:00",
          ageSeconds: 180,
          stale: true,
          source: "redis",
          refreshStatus: "queued",
        },
      },
    ]);
    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });
    await waitFor(() => {
      const options = watchlistQuoteObserverOptions(queryClient);
      expect(resolveOptionValue(options?.refetchInterval, queryClient)).toBe(
        WATCHLIST_QUOTE_REFETCH_INTERVAL_OPEN_MS,
      );
    });
  });

  it("keeps existing cards visible while quotes refetch", async () => {
    let resolveQuotes: ((value: QuoteResponse[]) => void) | undefined;
    vi.mocked(fetchQuotes)
      .mockResolvedValueOnce([quoteResponse])
      .mockImplementationOnce(
        () =>
          new Promise<QuoteResponse[]>((resolve) => {
            resolveQuotes = resolve;
          }),
      );

    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });

    void queryClient.refetchQueries({ queryKey: ["watchlist-quotes", ["TW:2330"]] });

    await waitFor(() => {
      expect(screen.getByTestId("watchlist-refreshing")).toBeInTheDocument();
    });
    expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();

    await act(async () => {
      resolveQuotes?.([quoteResponse]);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("watchlist-refreshing")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
  });

  it("does not refetch immediately on remount while data is still fresh", async () => {
    const queryClient = createQueryClient();
    const first = renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });
    const callsAfterFirstMount = vi.mocked(fetchQuotes).mock.calls.length;
    expect(callsAfterFirstMount).toBeGreaterThan(0);

    first.unmount();
    renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });
    expect(vi.mocked(fetchQuotes).mock.calls.length).toBe(callsAfterFirstMount);
  });

  it("refetches on remount after staleTime elapses", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const queryClient = createQueryClient();
    const first = renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });
    const callsAfterFirstMount = vi.mocked(fetchQuotes).mock.calls.length;

    first.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WATCHLIST_QUOTE_STALE_TIME_OPEN_MS + 1);
    });
    renderHome(queryClient);

    await waitFor(() => {
      expect(vi.mocked(fetchQuotes).mock.calls.length).toBeGreaterThan(callsAfterFirstMount);
    });
    expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
  });

  it("does not refetch on window focus while data is still fresh", async () => {
    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });
    const callsWhileFresh = vi.mocked(fetchQuotes).mock.calls.length;

    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });

    expect(vi.mocked(fetchQuotes).mock.calls.length).toBe(callsWhileFresh);
  });

  it("refetches on window focus after staleTime elapses", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });
    const callsAfterMount = vi.mocked(fetchQuotes).mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(WATCHLIST_QUOTE_STALE_TIME_OPEN_MS + 1);
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });

    await waitFor(() => {
      expect(vi.mocked(fetchQuotes).mock.calls.length).toBeGreaterThan(callsAfterMount);
    });
  });

  it("keeps previous quote cards while watchlist ids change", async () => {
    let resolveSecond: ((value: QuoteResponse[]) => void) | undefined;
    vi.mocked(fetchQuotes)
      .mockResolvedValueOnce([quoteResponse])
      .mockImplementationOnce(
        () =>
          new Promise<QuoteResponse[]>((resolve) => {
            resolveSecond = resolve;
          }),
      );

    vi.mocked(useWatchlist).mockReturnValue({
      ids: ["TW:2330"],
      hasLoaded: true,
      isLoading: false,
      isError: false,
      isMutating: false,
      error: null,
      mutationError: null,
      queryError: null,
      toggle: vi.fn(),
      remove: vi.fn(),
    });

    const queryClient = createQueryClient();
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <HomePage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    });

    vi.mocked(useWatchlist).mockReturnValue({
      ids: ["TW:2330", "TW:2317"],
      hasLoaded: true,
      isLoading: false,
      isError: false,
      isMutating: false,
      error: null,
      mutationError: null,
      queryError: null,
      toggle: vi.fn(),
      remove: vi.fn(),
    });
    rerender(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <HomePage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(fetchQuotes).toHaveBeenCalledWith(["TW:2330", "TW:2317"]);
    });
    expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();

    await act(async () => {
      resolveSecond?.([quoteResponse, secondQuoteResponse]);
    });
    await waitFor(() => {
      expect(screen.getByTestId("stock-card-TW:2317")).toBeInTheDocument();
    });
  });

  it("configures calmer market-indices polling than the legacy 5s open cadence", async () => {
    vi.mocked(fetchMarketIndices).mockResolvedValue([
      {
        id: "TWSE:TAIEX",
        market: "TW",
        label: "Taiwan Stock Exchange",
        value: 22000,
        change: 10,
        changePercent: 0.05,
        timestamp: "2026-07-13T10:00:00+08:00",
        isMarketOpen: true,
      },
    ]);
    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(fetchMarketIndices).toHaveBeenCalled();
    });
    await waitFor(() => {
      const query = queryClient.getQueryCache().find({ queryKey: ["market-indices"] });
      const options = query?.observers[0]?.options;
      expect(options?.staleTime).toBe(30_000);
      expect(options?.refetchIntervalInBackground).toBe(false);
      const interval =
        typeof options?.refetchInterval === "function"
          ? options.refetchInterval(query as never)
          : options?.refetchInterval;
      expect(interval).toBe(30_000);
    });
  });

  it("retries watchlist quotes through the batch refetch without background-fetch flagging", async () => {
    const user = userEvent.setup();
    let fetchCount = 0;
    vi.mocked(fetchQuotes).mockImplementation(async () => {
      fetchCount += 1;
      return [
        {
          ...quoteResponse,
          quote: null,
          freshness: {
            cachedAt: null,
            ageSeconds: null,
            stale: true,
            source: "none",
            refreshStatus: "unavailable",
          },
        },
      ];
    });
    const queryClient = createQueryClient();
    renderHome(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("watchlist-retry-quote")).toBeInTheDocument();
    });
    const callsBeforeRetry = fetchCount;
    await user.click(screen.getByTestId("watchlist-retry-quote"));
    await waitFor(() => {
      expect(fetchCount).toBeGreaterThan(callsBeforeRetry);
    });
    expect(screen.queryByTestId("watchlist-retrying-quote")).not.toBeInTheDocument();
  });

  it("ends a failed market summary on a retry instead of a spinner", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchMarkets).mockRejectedValue(new Error("backend down"));
    const queryClient = createQueryClient();
    renderHome(queryClient);

    const retry = await screen.findByRole("button", { name: /Retry|重試/ });
    expect(screen.queryByText(/Syncing market data|同步市場資料中/)).not.toBeInTheDocument();

    vi.mocked(fetchMarkets).mockResolvedValue([TW_OPEN, US_CLOSED]);
    await user.click(retry);
    await waitFor(() => {
      expect(screen.getByText(/Markets: 2|市場數: 2/)).toBeInTheDocument();
    });
  });
});
