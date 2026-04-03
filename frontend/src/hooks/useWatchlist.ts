"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addToWatchlist, fetchWatchlist, removeFromWatchlist } from "../services/api/client";

export function useWatchlist() {
  const queryClient = useQueryClient();
  const watchlistQuery = useQuery({
    queryKey: ["watchlist-ids"],
    queryFn: fetchWatchlist,
  });

  const addMutation = useMutation({
    mutationFn: addToWatchlist,
    onSuccess: (data) => {
      queryClient.setQueryData(["watchlist-ids"], data);
    },
  });

  const removeMutation = useMutation({
    mutationFn: removeFromWatchlist,
    onSuccess: (data) => {
      queryClient.setQueryData(["watchlist-ids"], data);
    },
  });

  const ids = watchlistQuery.data?.ids ?? [];

  return {
    ids,
    isLoading: watchlistQuery.isLoading,
    isMutating: addMutation.isPending || removeMutation.isPending,
    toggle: (stockId: string) => {
      if (ids.includes(stockId)) {
        removeMutation.mutate(stockId);
        return;
      }
      addMutation.mutate(stockId);
    },
    remove: (stockId: string) => removeMutation.mutate(stockId),
  };
}
