"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { PortfolioPosition } from "@shared/api";
import {
  addPortfolioPosition,
  fetchQuote,
  deletePortfolioPosition,
  fetchPortfolioPositions,
  updatePortfolioPosition,
} from "../services/api/client";

export function usePortfolioPositions() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["portfolio-positions"],
    queryFn: fetchPortfolioPositions,
    refetchInterval: 5000,
  });

  const addMutation = useMutation({
    mutationFn: addPortfolioPosition,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio-positions"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ positionId, payload }: { positionId: number; payload: Parameters<typeof updatePortfolioPosition>[1] }) =>
      updatePortfolioPosition(positionId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio-positions"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deletePortfolioPosition,
    onSuccess: (_, positionId) => {
      // Remove locally first: refetch can fail if another row has a missing quote
      // (GET /portfolio/positions raises 400 for the whole list), which would leave stale rows.
      queryClient.setQueryData<PortfolioPosition[]>(["portfolio-positions"], (old) =>
        (old ?? []).filter((p) => p.id !== positionId),
      );
      queryClient.invalidateQueries({ queryKey: ["portfolio-positions"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio-summary"] });
    },
  });

  const positions: PortfolioPosition[] = query.data ?? [];

  const addByStockId = async (stockId: string) => {
    const quote = await fetchQuote(stockId);
    addMutation.mutate({
      stockId,
      quantity: 1,
      averageCost: quote.quote.lastPrice,
      currency: quote.stock.currency,
      notes: null,
    });
  };

  return {
    positions,
    ids: positions.map((item) => item.stockId),
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    isMutating: addMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
    add: addMutation.mutate,
    addByStockId,
    update: updateMutation.mutate,
    remove: deleteMutation.mutate,
  };
}
