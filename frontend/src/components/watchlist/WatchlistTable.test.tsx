import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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
  });
});
