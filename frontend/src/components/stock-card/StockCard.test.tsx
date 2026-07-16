import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
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
    pushMock.mockReset();
    fetchHistoryMock.mockReset();
    fetchHistoryMock.mockResolvedValue({
      stock,
      range: "1W",
      candles: [],
      chartLayout: null,
    });
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
});

