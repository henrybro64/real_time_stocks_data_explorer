"use client";

import type { RealizedTradeCurrency, RealizedTradeUpdate } from "@shared/api";
import {
  createRealizedTrade,
  deleteRealizedTrade,
  fetchRealizedTrades,
  updateRealizedTrade,
} from "./realizedTradesClient";
import { useCrudResource } from "../../hooks/useCrudResource";

export function useRealizedTrades(currency: RealizedTradeCurrency) {
  const resource = useCrudResource({
    queryKey: ["realized-trades", currency],
    queryFn: () => fetchRealizedTrades(currency),
    createItem: createRealizedTrade,
    updateItem: updateRealizedTrade,
    deleteItem: deleteRealizedTrade,
  });

  return {
    trades: resource.items,
    tradesQuery: resource.listQuery,
    createMutation: resource.createMutation,
    updateMutation: resource.updateMutation,
    deleteMutation: resource.deleteMutation,
    mutationError: resource.mutationError,
    isMutating: resource.isMutating,
  };
}
