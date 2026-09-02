"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  RealizedTradeCreate,
  RealizedTradeCurrency,
  RealizedTradeUpdate,
} from "@shared/api";
import {
  createRealizedTrade,
  deleteRealizedTrade,
  fetchRealizedTrades,
  updateRealizedTrade,
} from "../services/api/client";

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

  const createMutation = useMutation({
    mutationFn: (payload: RealizedTradeCreate) => createRealizedTrade(payload),
    onSuccess: () => {
      invalidateTrades();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ tradeId, payload }: { tradeId: number; payload: RealizedTradeUpdate }) =>
      updateRealizedTrade(tradeId, payload),
    onSuccess: () => {
      invalidateTrades();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRealizedTrade,
    onSuccess: () => {
      invalidateTrades();
    },
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
