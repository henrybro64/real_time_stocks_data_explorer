"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  RealizedTradeCurrency,
  RealizedTradeUpdate,
} from "@shared/api";
import {
  createRealizedTrade,
  deleteRealizedTrade,
  fetchRealizedTrades,
  updateRealizedTrade,
} from "../views/realized-gains/realizedTradesClient";

export function useRealizedTrades(currency: RealizedTradeCurrency) {
  const queryClient = useQueryClient();
  const queryKey = ["realized-trades", currency] as const;

  const tradesQuery = useQuery({
    queryKey,
    queryFn: () => fetchRealizedTrades(currency),
  });

  const invalidateTrades = () => {
    queryClient.invalidateQueries({ queryKey });
  };
  const mutationOptions = { onSuccess: invalidateTrades };

  const createMutation = useMutation({
    mutationFn: createRealizedTrade,
    ...mutationOptions,
  });

  const updateMutation = useMutation({
    mutationFn: ({ tradeId, payload }: { tradeId: number; payload: RealizedTradeUpdate }) =>
      updateRealizedTrade(tradeId, payload),
    ...mutationOptions,
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRealizedTrade,
    ...mutationOptions,
  });

  return {
    trades: tradesQuery.data ?? [],
    tradesQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    mutationError:
      createMutation.error ?? updateMutation.error ?? deleteMutation.error,
    isMutating:
      createMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
  };
}
