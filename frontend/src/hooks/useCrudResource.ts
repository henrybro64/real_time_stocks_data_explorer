"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

type CrudResourceConfig<TItem, TCreate, TUpdate> = Readonly<{
  queryKey: readonly unknown[];
  queryFn: () => Promise<TItem[]>;
  createItem: (payload: TCreate) => Promise<unknown>;
  updateItem: (tradeId: number, payload: TUpdate) => Promise<unknown>;
  deleteItem: (tradeId: number) => Promise<unknown>;
}>;

export function useCrudResource<TItem, TCreate, TUpdate>({
  queryKey,
  queryFn,
  createItem,
  updateItem,
  deleteItem,
}: CrudResourceConfig<TItem, TCreate, TUpdate>) {
  const queryClient = useQueryClient();
  const listQuery = useQuery({ queryKey, queryFn });
  const invalidateList = () => {
    queryClient.invalidateQueries({ queryKey });
  };
  const mutationOptions = { onSuccess: invalidateList };

  const createMutation = useMutation({
    mutationFn: createItem,
    ...mutationOptions,
  });
  const updateMutation = useMutation({
    mutationFn: ({ tradeId, payload }: { tradeId: number; payload: TUpdate }) =>
      updateItem(tradeId, payload),
    ...mutationOptions,
  });
  const deleteMutation = useMutation({
    mutationFn: deleteItem,
    ...mutationOptions,
  });

  return {
    items: listQuery.data ?? [],
    listQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    mutationError:
      createMutation.error ?? updateMutation.error ?? deleteMutation.error,
    isMutating:
      createMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
  };
}
