"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

type CrudResourceConfig<TItem, TCreate, TUpdate> = Readonly<{
  queryKey: readonly unknown[];
  queryFn: () => Promise<TItem[]>;
  createItem: (payload: TCreate) => Promise<unknown>;
  updateItem: (itemId: number, payload: TUpdate) => Promise<unknown>;
  deleteItem: (itemId: number) => Promise<unknown>;
  refetchInterval?: number;
  onMutationSuccess?: () => void;
}>;

export function useCrudResource<TItem, TCreate, TUpdate>({
  queryKey,
  queryFn,
  createItem,
  updateItem,
  deleteItem,
  refetchInterval,
  onMutationSuccess,
}: CrudResourceConfig<TItem, TCreate, TUpdate>) {
  const queryClient = useQueryClient();
  const listQuery = useQuery({
    queryKey,
    queryFn,
    refetchInterval,
  });
  const invalidateList = onMutationSuccess ?? (() => {
    queryClient.invalidateQueries({ queryKey });
  });
  const mutationOptions = { onSuccess: invalidateList };

  const createMutation = useMutation({
    mutationFn: createItem,
    ...mutationOptions,
  });
  const updateMutation = useMutation({
    mutationFn: ({ itemId, payload }: { itemId: number; payload: TUpdate }) =>
      updateItem(itemId, payload),
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
