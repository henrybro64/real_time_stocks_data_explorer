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

  const resetMutations = () => {
    addMutation.reset();
    removeMutation.reset();
  };

  const ids = watchlistQuery.data?.ids ?? [];
  const mutationError = addMutation.error ?? removeMutation.error;

  return {
    ids,
    hasLoaded: watchlistQuery.data !== undefined,
    isLoading: watchlistQuery.isLoading,
    isError: watchlistQuery.isError || addMutation.isError || removeMutation.isError,
    isMutating: addMutation.isPending || removeMutation.isPending,
    error: mutationError ?? watchlistQuery.error,
    mutationError,
    queryError: watchlistQuery.error,
    toggle: (stockId: string) => {
      resetMutations();
      if (ids.includes(stockId)) {
        removeMutation.mutate(stockId);
        return;
      }
      addMutation.mutate(stockId);
    },
    remove: (stockId: string) => {
      resetMutations();
      removeMutation.mutate(stockId);
    },
  };
}
