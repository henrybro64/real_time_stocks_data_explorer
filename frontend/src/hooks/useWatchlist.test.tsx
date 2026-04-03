import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useWatchlist } from "./useWatchlist";
import { addToWatchlist, fetchWatchlist, removeFromWatchlist } from "../services/api/client";

vi.mock("../services/api/client", () => ({
  fetchWatchlist: vi.fn(),
  addToWatchlist: vi.fn(),
  removeFromWatchlist: vi.fn(),
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

describe("useWatchlist", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("adds a stock when toggling an untracked id", async () => {
    vi.mocked(fetchWatchlist).mockResolvedValue({ ids: ["US:AAPL"] });
    vi.mocked(addToWatchlist).mockResolvedValue({ ids: ["US:AAPL", "TW:2330"] });

    const { result } = renderHook(() => useWatchlist(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.ids).toEqual(["US:AAPL"]);
    });

    act(() => {
      result.current.toggle("TW:2330");
    });

    await waitFor(() => {
      expect(addToWatchlist).toHaveBeenCalled();
      expect(vi.mocked(addToWatchlist).mock.calls[0]?.[0]).toBe("TW:2330");
      expect(result.current.ids).toEqual(["US:AAPL", "TW:2330"]);
    });
  });

  it("removes a stock when toggling an already tracked id", async () => {
    vi.mocked(fetchWatchlist).mockResolvedValue({ ids: ["US:AAPL", "TW:2330"] });
    vi.mocked(removeFromWatchlist).mockResolvedValue({ ids: ["TW:2330"] });

    const { result } = renderHook(() => useWatchlist(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.ids).toEqual(["US:AAPL", "TW:2330"]);
    });

    act(() => {
      result.current.toggle("US:AAPL");
    });

    await waitFor(() => {
      expect(removeFromWatchlist).toHaveBeenCalled();
      expect(vi.mocked(removeFromWatchlist).mock.calls[0]?.[0]).toBe("US:AAPL");
      expect(result.current.ids).toEqual(["TW:2330"]);
    });
  });
});
