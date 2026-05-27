"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AlertRuleCreate, AlertRuleUpdate } from "@shared/api";
import {
  createAlertRule,
  deleteAlertRule,
  fetchAlertEvents,
  fetchAlertRules,
  updateAlertRule,
} from "../services/api/client";


export function useAlerts() {
  const queryClient = useQueryClient();

  const invalidateAlerts = () => {
    queryClient.invalidateQueries({ queryKey: ["alert-rules"] });
    queryClient.invalidateQueries({ queryKey: ["alert-events"] });
  };

  const rulesQuery = useQuery({
    queryKey: ["alert-rules"],
    queryFn: fetchAlertRules,
    refetchInterval: 30_000,
  });

  const eventsQuery = useQuery({
    queryKey: ["alert-events"],
    queryFn: () => fetchAlertEvents(20),
    refetchInterval: 30_000,
  });

  const createMutation = useMutation({
    mutationFn: (payload: AlertRuleCreate) => createAlertRule(payload),
    onSuccess: () => {
      invalidateAlerts();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ ruleId, payload }: { ruleId: number; payload: AlertRuleUpdate }) =>
      updateAlertRule(ruleId, payload),
    onSuccess: () => {
      invalidateAlerts();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAlertRule,
    onSuccess: () => {
      invalidateAlerts();
    },
  });

  const resetMutations = () => {
    createMutation.reset();
    updateMutation.reset();
    deleteMutation.reset();
  };

  return {
    rules: rulesQuery.data ?? [],
    events: eventsQuery.data ?? [],
    rulesQuery,
    eventsQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    mutationError: createMutation.error ?? updateMutation.error ?? deleteMutation.error,
    isMutating:
      createMutation.isPending ||
      updateMutation.isPending ||
      deleteMutation.isPending,
    create: (payload: AlertRuleCreate) => {
      resetMutations();
      createMutation.mutate(payload);
    },
    update: (ruleId: number, payload: AlertRuleUpdate) => {
      resetMutations();
      updateMutation.mutate({ ruleId, payload });
    },
    remove: (ruleId: number) => {
      resetMutations();
      deleteMutation.mutate(ruleId);
    },
  };
}
