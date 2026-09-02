import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createRealizedTrade,
  deleteRealizedTrade,
  fetchRealizedTrades,
  updateRealizedTrade,
} from "../../../src/services/api/client";
import { buildSampleRealizedTrade } from "../../../src/views/realized-gains/realizedTradeTestFixtures";
import { useRealizedGainsPageState } from "../../../src/views/realized-gains/useRealizedGainsPageState";

const useQueryOptions: unknown[] = [];

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  );
  return {
    ...actual,
    useQuery: (options: unknown) => {
      useQueryOptions.push(options);
      return actual.useQuery(options as never);
    },
  };
});

vi.mock("../../../src/services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../../src/services/api/client")>(
    "../../../src/services/api/client",
  );
  return {
    ...actual,
    fetchRealizedTrades: vi.fn(),
    createRealizedTrade: vi.fn(),
    updateRealizedTrade: vi.fn(),
    deleteRealizedTrade: vi.fn(),
  };
});

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useRealizedGainsPageState trades query", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useQueryOptions.length = 0;
    vi.mocked(fetchRealizedTrades).mockResolvedValue([]);
  });

  it("queries trades for the selected currency", async () => {
    renderHook(() => useRealizedGainsPageState("USD"), { wrapper });
    await waitFor(() => {
      expect(fetchRealizedTrades).toHaveBeenCalledWith("USD");
    });
  });

  it("does not configure periodic polling", async () => {
    renderHook(() => useRealizedGainsPageState("TWD"), { wrapper });
    await waitFor(() => {
      expect(useQueryOptions.length).toBeGreaterThan(0);
    });
    const realizedQuery = useQueryOptions.find(
      (options) =>
        typeof options === "object" &&
        options != null &&
        Array.isArray((options as { queryKey?: unknown }).queryKey) &&
        (options as { queryKey: unknown[] }).queryKey[0] === "realized-trades",
    ) as { refetchInterval?: unknown } | undefined;
    expect(realizedQuery?.refetchInterval).toBeUndefined();
  });

  it("invalidates trades after create, update, and delete", async () => {
    const baseTrade = buildSampleRealizedTrade();
    vi.mocked(createRealizedTrade).mockResolvedValue(baseTrade);
    vi.mocked(updateRealizedTrade).mockResolvedValue(
      buildSampleRealizedTrade({ sellPrice: 1010, realizedGain: 110000, realizedReturnPercent: 12.2222 }),
    );
    vi.mocked(deleteRealizedTrade).mockResolvedValue(undefined);

    const { result } = renderHook(() => useRealizedGainsPageState("TWD"), { wrapper });
    await waitFor(() => expect(fetchRealizedTrades).toHaveBeenCalledTimes(1));

    await result.current.realized.createMutation.mutateAsync({
      symbol: "2330",
      currency: "TWD",
      buyPrice: 900,
      sellPrice: 1000,
      shares: 1000,
      sellDate: "2026-08-01",
    });
    expect(createRealizedTrade).toHaveBeenCalled();
    await waitFor(() => expect(vi.mocked(fetchRealizedTrades).mock.calls.length).toBeGreaterThanOrEqual(2));

    await result.current.realized.updateMutation.mutateAsync({
      itemId: 1,
      payload: { sellPrice: 1010 },
    });
    expect(updateRealizedTrade).toHaveBeenCalled();
    await waitFor(() => expect(vi.mocked(fetchRealizedTrades).mock.calls.length).toBeGreaterThanOrEqual(3));

    await result.current.realized.deleteMutation.mutateAsync(1);
    expect(deleteRealizedTrade).toHaveBeenCalled();
    await waitFor(() => expect(vi.mocked(fetchRealizedTrades).mock.calls.length).toBeGreaterThanOrEqual(4));
  });
});
