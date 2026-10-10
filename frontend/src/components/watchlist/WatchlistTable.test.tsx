import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Stock } from "@shared/stock";

import { LanguageProvider } from "../../app/i18n";
import { WatchlistTable } from "./WatchlistTable";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchHistory: vi.fn().mockResolvedValue({
      stockId: "TW:2330",
      range: "1W",
      candles: [],
      chartLayout: null,
      movingAverages: [],
    }),
    fetchWatchlistSparklines: vi.fn().mockResolvedValue({}),
  };
});

function renderWithLanguage(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>{ui}</LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("WatchlistTable", () => {
  it("keeps pending tracked symbols visible when quote hydration fails", async () => {
    const onRemove = vi.fn();
    const user = userEvent.setup();

    renderWithLanguage(
      <WatchlistTable
        items={[]}
        noticeKind="error"
        onRemove={onRemove}
        pendingIds={["TW:2330"]}
        trackedCount={1}
      />,
    );

    expect(screen.getByText("載入失敗")).toBeInTheDocument();
    expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();

    await user.click(screen.getByTestId("stock-card-toggle-TW:2330"));

    expect(onRemove).toHaveBeenCalledWith("TW:2330");
  });

  it("shows only the notice when there are no hydrated or pending watchlist entries", () => {
    renderWithLanguage(
      <WatchlistTable
        items={[]}
        noticeKind="unavailable"
        onRemove={() => {}}
        pendingIds={[]}
        trackedCount={0}
      />,
    );

    expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
    expect(
      screen.queryByText("搜尋股票後加入清單，這裡會持續顯示最新報價與更新時間。"),
    ).not.toBeInTheDocument();
  });

  it("keeps quote cards visible while a background refresh is in progress", () => {
    renderWithLanguage(
      <WatchlistTable
        isRefreshing
        items={[
          {
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
          },
        ]}
        onRemove={() => {}}
        trackedCount={1}
      />,
    );

    expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    expect(screen.getByText("同步市場資料中")).toBeInTheDocument();
    expect(screen.queryByText("載入自選報價中")).not.toBeInTheDocument();
    expect(screen.getByTestId("watchlist-market-TW")).toHaveTextContent("2330.TW");
    expect(screen.queryByTestId("watchlist-market-US")).not.toBeInTheDocument();
  });

  it("groups .TW, .TWO, and U.S. symbols into market sections", () => {
    renderWithLanguage(
      <WatchlistTable
        items={[
          item(stock({ id: "US:MSFT", market: "US", symbol: "MSFT", name: "Microsoft", exchange: "NASDAQ", currency: "USD", timezone: "America/New_York" })),
          item(stock({ id: "TW:2330", market: "TW", symbol: "2330", name: "台積電", exchange: "TWSE", nameZhTw: "台積電" })),
          item(stock({ id: "TW:3081", market: "TW", symbol: "3081", name: "聯亞", exchange: "TPEx", nameZhTw: "聯亞" })),
          item(stock({ id: "US:NVDA", market: "US", symbol: "NVDA", name: "NVIDIA", exchange: "NASDAQ", currency: "USD", timezone: "America/New_York", nameEn: "NVIDIA" })),
        ]}
        onRemove={() => {}}
        trackedCount={4}
      />,
    );

    const taiwan = screen.getByTestId("watchlist-market-TW");
    const us = screen.getByTestId("watchlist-market-US");
    expect(taiwan).toHaveTextContent("台灣股票");
    expect(taiwan).toHaveTextContent("2 檔");
    expect(taiwan).toHaveTextContent("2330.TW");
    expect(taiwan).toHaveTextContent("3081.TWO");
    expect(within(taiwan).getByTestId("stock-card-TW:2330")).toBeInTheDocument();
    expect(within(taiwan).getByTestId("stock-card-TW:3081")).toBeInTheDocument();
    expect(us).toHaveTextContent("美股");
    expect(us).toHaveTextContent("2 檔");
    expect(us).toHaveTextContent("NVDA");
    expect(us).toHaveTextContent("MSFT");
    expect(within(us).getByTestId("stock-card-US:NVDA")).toBeInTheDocument();
    expect(within(taiwan).queryByTestId("stock-card-US:NVDA")).not.toBeInTheDocument();
    expect(screen.queryByTestId("watchlist-market-unknown")).not.toBeInTheDocument();
  });

  it("hides an empty market section and keeps an unknown symbol visible", () => {
    renderWithLanguage(
      <WatchlistTable
        items={[
          item(stock({ id: "US:AAPL", market: "US", symbol: "AAPL", name: "Apple", exchange: "NASDAQ", currency: "USD", timezone: "America/New_York" })),
        ]}
        onRemove={() => {}}
        pendingIds={["not-a-symbol"]}
        trackedCount={2}
      />,
    );

    expect(screen.queryByTestId("watchlist-market-TW")).not.toBeInTheDocument();
    expect(screen.getByTestId("watchlist-market-US")).toHaveTextContent("AAPL");
    expect(screen.getByTestId("watchlist-market-unknown")).toHaveTextContent("其他代碼");
    expect(screen.getByTestId("stock-card-not-a-symbol")).toBeInTheDocument();
  });

  it("shows the existing empty state when the profile has no symbols", () => {
    renderWithLanguage(<WatchlistTable items={[]} onRemove={() => {}} />);

    expect(screen.getByText("搜尋股票後加入清單，這裡會持續顯示最新報價與更新時間。")).toBeInTheDocument();
    expect(screen.queryByTestId("watchlist-market-TW")).not.toBeInTheDocument();
    expect(screen.queryByTestId("watchlist-market-US")).not.toBeInTheDocument();
  });

  it("removes a symbol from its market section", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    const taiwan = item(stock({ id: "TW:2330", market: "TW", symbol: "2330", name: "台積電", exchange: "TWSE" }));
    const us = item(stock({ id: "US:AAPL", market: "US", symbol: "AAPL", name: "Apple", exchange: "NASDAQ", currency: "USD", timezone: "America/New_York" }));
    const { rerender } = renderWithLanguage(
      <WatchlistTable items={[taiwan, us]} onRemove={onRemove} trackedCount={2} />,
    );

    await user.click(screen.getByTestId("stock-card-toggle-TW:2330"));
    expect(onRemove).toHaveBeenCalledWith("TW:2330");

    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <LanguageProvider>
          <WatchlistTable items={[us]} onRemove={onRemove} trackedCount={1} />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(screen.queryByTestId("watchlist-market-TW")).not.toBeInTheDocument();
    expect(screen.queryByTestId("stock-card-TW:2330")).not.toBeInTheDocument();
    expect(screen.getByTestId("watchlist-market-US")).toBeInTheDocument();
  });

  it("uses English section labels", () => {
    window.localStorage.setItem("stock-ticker-language", "en");
    renderWithLanguage(
      <WatchlistTable
        items={[item(stock({ id: "TW:2454", market: "TW", symbol: "2454", name: "聯發科", exchange: "TWSE" }))]}
        onRemove={() => {}}
        trackedCount={1}
      />,
    );

    expect(screen.getByTestId("watchlist-market-TW")).toHaveTextContent("Taiwan Stocks");
    expect(screen.getByTestId("watchlist-market-TW")).toHaveTextContent("1 symbol");
    expect(screen.getByTestId("watchlist-market-TW")).not.toHaveTextContent("1 symbols");
    expect(screen.getByTestId("watchlist-market-TW")).toHaveTextContent("2454.TW");
  });

  it("uses the English plural when a section has several symbols", () => {
    window.localStorage.setItem("stock-ticker-language", "en");
    renderWithLanguage(
      <WatchlistTable
        items={[
          item(stock({ id: "US:NVDA", market: "US", symbol: "NVDA", name: "NVIDIA", exchange: "NASDAQ", currency: "USD", timezone: "America/New_York" })),
          item(stock({ id: "US:MSFT", market: "US", symbol: "MSFT", name: "Microsoft", exchange: "NASDAQ", currency: "USD", timezone: "America/New_York" })),
        ]}
        onRemove={() => {}}
        trackedCount={2}
      />,
    );

    expect(screen.getByTestId("watchlist-market-US")).toHaveTextContent("2 symbols");
  });
});

function stock(partial: Partial<Stock> & Pick<Stock, "id" | "market" | "symbol" | "name" | "exchange">): Stock {
  return {
    currency: "TWD",
    timezone: "Asia/Taipei",
    ...partial,
  };
}

function item(nextStock: Stock) {
  return { stock: nextStock, quote: null };
}
