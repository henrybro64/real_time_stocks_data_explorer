import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { QuoteResponse } from "@shared/api";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { StockDetailPage } from "./StockDetailPage";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
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

function renderStockDetailPage() {
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
        <StockDetailPage stockId="US%3ANVDA" />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("StockDetailPage", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-21T12:00:00Z"));
    useQueryMock.mockReset();
    useQueryMock.mockImplementation((options: { queryKey: unknown[] }) => {
      if (options.queryKey[0] === "quote") {
        return {
          data: sampleQuote,
          isError: true,
          isLoading: false,
        };
      }

      return {
        data: undefined,
        isError: false,
        isLoading: false,
      };
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows stale notice and stale freshness badge when quote refresh fails with cached data", () => {
    renderStockDetailPage();

    expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("過舊");
  });
});
