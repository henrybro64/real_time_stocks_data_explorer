"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { PortfolioPosition } from "@shared/api";
import {
  addPortfolioPosition,
  fetchQuote,
  deletePortfolioPosition,
  fetchPortfolioPositions,
  updatePortfolioPosition,
} from "../services/api/client";
import { useQuoteDerivedRefetchInterval } from "./useQuoteDerivedRefetchInterval";

export function usePortfolioPositions() {
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<unknown>(null);
  const cachedPositions =
    queryClient.getQueryData<PortfolioPosition[]>(["portfolio-positions"]) ?? [];
  const refetchInterval = useQuoteDerivedRefetchInterval(
    cachedPositions.map((item) => item.stockId),
  );
  const invalidatePortfolioQueries = () => {
    queryClient.invalidateQueries({ queryKey: ["portfolio-positions"] });
    queryClient.invalidateQueries({ queryKey: ["portfolio-summary"] });
    queryClient.invalidateQueries({ queryKey: ["portfolio-holdings-history"] });
    queryClient.invalidateQueries({ queryKey: ["portfolio-combined-assets-history"] });
  };
  const query = useQuery({
    queryKey: ["portfolio-positions"],
    queryFn: fetchPortfolioPositions,
    refetchInterval,
    refetchIntervalInBackground: false,
  });

  const addMutation = useMutation({
    mutationFn: addPortfolioPosition,
    onSuccess: () => {
      invalidatePortfolioQueries();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ positionId, payload }: { positionId: number; payload: Parameters<typeof updatePortfolioPosition>[1] }) =>
      updatePortfolioPosition(positionId, payload),
    onSuccess: () => {
      invalidatePortfolioQueries();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deletePortfolioPosition,
    onSuccess: (_, positionId) => {
      // Remove locally first so the table updates immediately before refetch.
      queryClient.setQueryData<PortfolioPosition[]>(["portfolio-positions"], (old) =>
        (old ?? []).filter((p) => p.id !== positionId),
      );
      invalidatePortfolioQueries();
    },
  });

  const positions: PortfolioPosition[] = query.data ?? [];
  const mutationError = addMutation.error ?? updateMutation.error ?? deleteMutation.error;

  const resetErrors = () => {
    addMutation.reset();
    updateMutation.reset();
    deleteMutation.reset();
    setActionError(null);
  };

  const addByStockId = async (stockId: string) => {
    resetErrors();
    try {
      let quote = await fetchQuote(stockId);
      // Cold miss while the background refresher owns upstream: wait longer than
      // max jitter (2s) plus network/queue slack before giving up.
      if (quote.quote == null) {
        await new Promise((resolve) => {
          window.setTimeout(resolve, 4000);
        });
        quote = await fetchQuote(stockId);
      }
      if (quote.quote == null) {
        throw new Error("Quote unavailable");
      }
      addMutation.mutate({
        stockId,
        quantity: 1,
        averageCost: quote.quote.lastPrice,
        currency: quote.stock.currency,
        notes: null,
      });
    } catch (error) {
      setActionError(error);
    }
  };

  const add: typeof addMutation.mutate = (...args) => {
    resetErrors();
    addMutation.mutate(...args);
  };

  const update: typeof updateMutation.mutate = (...args) => {
    resetErrors();
    updateMutation.mutate(...args);
  };

  const remove: typeof deleteMutation.mutate = (...args) => {
    resetErrors();
    deleteMutation.mutate(...args);
  };

  return {
    positions,
    ids: positions.map((item) => item.stockId),
    isLoading: query.isLoading,
    isError:
      query.isError ||
      addMutation.isError ||
      updateMutation.isError ||
      deleteMutation.isError ||
      actionError != null,
    error: actionError ?? mutationError ?? query.error,
    queryError: query.error,
    mutationError,
    actionError,
    isMutating: addMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
    add,
    addByStockId,
    update,
    remove,
  };
}
