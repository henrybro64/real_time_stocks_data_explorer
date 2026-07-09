import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { StockCard } from "./StockCard";

const pushMock = vi.fn();

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
    fetchHistory: vi.fn().mockResolvedValue({
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
      range: "1W",
      candles: [],
      chartLayout: null,
    }),
  };
});

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
});

