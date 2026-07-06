import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PortfolioPosition } from "@shared/api";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePortfolioPositions } from "./usePortfolioPositions";
import {
  addPortfolioPosition,
  deletePortfolioPosition,
  fetchPortfolioPositions,
  fetchQuote,
  updatePortfolioPosition,
} from "../services/api/client";

vi.mock("../services/api/client", () => ({
  addPortfolioPosition: vi.fn(),
  fetchQuote: vi.fn(),
  deletePortfolioPosition: vi.fn(),
  fetchPortfolioPositions: vi.fn(),
  updatePortfolioPosition: vi.fn(),
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function buildPosition(id: number, stockId: string): PortfolioPosition {
  return {
    id,
    stockId,
    quantity: 2,
    averageCost: 100,
    currentPrice: 105,
    costBasis: 200,
    currency: "USD",
    notes: null,
    stock: {
      id: stockId,
      market: "US",
      symbol: "AAPL",
      name: "Apple Inc.",
      exchange: "NASDAQ",
      currency: "USD",
      timezone: "America/New_York",
      nameEn: "Apple Inc.",
      nameZhTw: "蘋果",
    },
    quote: {
      id: stockId,
      lastPrice: 105,
      change: 1,
      changePercent: 1,
      previousClose: 104,
      open: 104,
      high: 106,
      low: 103,
      volume: 1000,
      timestamp: "2026-04-01T09:30:00-04:00",
      isMarketOpen: true,
    },
    marketValue: 210,
    unrealizedPl: 10,
    unrealizedPlPercent: 5,
  };
}

describe("usePortfolioPositions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("builds a default add payload from the latest quote", async () => {
    vi.mocked(fetchPortfolioPositions).mockResolvedValue([]);
    vi.mocked(fetchQuote).mockResolvedValue({
      stock: {
        id: "US:AAPL",
        market: "US",
        symbol: "AAPL",
        name: "Apple Inc.",
        exchange: "NASDAQ",
        currency: "USD",
        timezone: "America/New_York",
        nameEn: "Apple Inc.",
        nameZhTw: "蘋果",
      },
      quote: {
        id: "US:AAPL",
        lastPrice: 187.23,
        change: 1.5,
        changePercent: 0.8,
        previousClose: 185.73,
        timestamp: "2026-04-01T09:30:00-04:00",
        isMarketOpen: true,
        open: 186,
        high: 188,
        low: 185,
        volume: 1000,
      },
    });
    vi.mocked(addPortfolioPosition).mockResolvedValue(buildPosition(1, "US:AAPL"));

    const { result } = renderHook(() => usePortfolioPositions(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.positions).toEqual([]);
    });

    await act(async () => {
      await result.current.addByStockId("US:AAPL");
    });

    await waitFor(() => {
      expect(fetchQuote).toHaveBeenCalledWith("US:AAPL");
      expect(addPortfolioPosition).toHaveBeenCalled();
      expect(vi.mocked(addPortfolioPosition).mock.calls[0]?.[0]).toEqual({
        stockId: "US:AAPL",
        quantity: 1,
        averageCost: 187.23,
        currency: "USD",
        notes: null,
      });
    });
  });

  it("removes the deleted position from the local query cache", async () => {
    const first = buildPosition(1, "US:AAPL");
    const second = buildPosition(2, "US:MSFT");
    vi.mocked(fetchPortfolioPositions)
      .mockResolvedValueOnce([first, second])
      .mockResolvedValueOnce([second]);
    vi.mocked(deletePortfolioPosition).mockResolvedValue(undefined);
    vi.mocked(updatePortfolioPosition).mockResolvedValue(first);

    const { result } = renderHook(() => usePortfolioPositions(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.positions).toHaveLength(2);
    });

    act(() => {
      result.current.remove(1);
    });

    await waitFor(() => {
      expect(deletePortfolioPosition).toHaveBeenCalled();
      expect(vi.mocked(deletePortfolioPosition).mock.calls[0]?.[0]).toBe(1);
      expect(result.current.positions).toHaveLength(1);
      expect(result.current.positions[0]?.stockId).toBe("US:MSFT");
    });
  });

  it("surfaces add-by-symbol quote lookup failures as action errors", async () => {
    vi.mocked(fetchPortfolioPositions).mockResolvedValue([]);
    vi.mocked(fetchQuote).mockRejectedValue(new Error("Failed to fetch"));

    const { result } = renderHook(() => usePortfolioPositions(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.positions).toEqual([]);
    });

    await act(async () => {
      await result.current.addByStockId("US:AAPL");
    });

    await waitFor(() => {
      expect(result.current.actionError).toBeInstanceOf(Error);
      expect((result.current.actionError as Error).message).toBe("Failed to fetch");
    });

    expect(addPortfolioPosition).not.toHaveBeenCalled();
  });
});
