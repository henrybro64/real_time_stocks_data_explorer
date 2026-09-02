import type { RealizedTrade, RealizedTradeCurrency } from "@shared/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { errorToNoticeKind } from "../../components/state/RequestStateNotice";
import {
  createRealizedTrade,
  deleteRealizedTrade,
  fetchRealizedTrades,
  toApiError,
  updateRealizedTrade,
} from "../../services/api/client";
import {
  buildMonthlyRealizedGainSeries,
  previewRealizedGain,
} from "./realizedGainCalculations";

export type RealizedGainsFormState = {
  symbol: string;
  buyPrice: string;
  sellPrice: string;
  shares: string;
  buyDate: string;
  sellDate: string;
};

function resolveTradesNoticeKind(error: unknown, tradeCount: number) {
  if (error == null) {
    return null;
  }
  if (tradeCount > 0) {
    return "stale" as const;
  }
  return errorToNoticeKind(error);
}

export const EMPTY_REALIZED_GAINS_FORM: RealizedGainsFormState = {
  symbol: "",
  buyPrice: "",
  sellPrice: "",
  shares: "",
  buyDate: "",
  sellDate: "",
};

export function useRealizedGainsPageState(initialCurrency: RealizedTradeCurrency = "TWD") {
  const [currency, setCurrency] = useState<RealizedTradeCurrency>(initialCurrency);
  const queryClient = useQueryClient();
  const tradesQuery = useQuery({
    queryKey: ["realized-trades", currency],
    queryFn: () => fetchRealizedTrades(currency),
  });
  const invalidateTrades = () => {
    queryClient.invalidateQueries({ queryKey: ["realized-trades", currency] });
  };
  const createMutation = useMutation({
    mutationFn: createRealizedTrade,
    onSuccess: invalidateTrades,
  });
  const updateMutation = useMutation({
    mutationFn: ({ itemId, payload }: { itemId: number; payload: Parameters<typeof updateRealizedTrade>[1] }) =>
      updateRealizedTrade(itemId, payload),
    onSuccess: invalidateTrades,
  });
  const deleteMutation = useMutation({
    mutationFn: deleteRealizedTrade,
    onSuccess: invalidateTrades,
  });
  const realized = {
    trades: tradesQuery.data ?? [],
    tradesQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    mutationError: createMutation.error ?? updateMutation.error ?? deleteMutation.error,
    isMutating:
      createMutation.isPending || updateMutation.isPending || deleteMutation.isPending,
  };
  const [form, setForm] = useState<RealizedGainsFormState>(EMPTY_REALIZED_GAINS_FORM);
  const [editingTradeId, setEditingTradeId] = useState<number | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const pendingDeleteIdRef = useRef<number | null>(null);

  const today = useMemo(() => new Date(), []);
  const preview = previewRealizedGain(
    Number(form.buyPrice),
    Number(form.sellPrice),
    Number(form.shares),
  );
  const monthlySeries = useMemo(
    () => buildMonthlyRealizedGainSeries(realized.trades, today),
    [realized.trades, today],
  );
  const maxAbsMonthlyGain = Math.max(
    1,
    ...monthlySeries.map((point) => Math.abs(point.totalGain)),
  );

  const noticeKind = resolveTradesNoticeKind(
    realized.tradesQuery.error,
    realized.trades.length,
  );
  const formMutationError =
    realized.createMutation.error ?? realized.updateMutation.error;
  const formMutationApiError =
    formMutationError != null ? toApiError(formMutationError) : null;
  const formMutationNoticeKind =
    formMutationApiError != null ? errorToNoticeKind(formMutationApiError) : null;
  const deleteMutationApiError =
    realized.deleteMutation.error != null ? toApiError(realized.deleteMutation.error) : null;
  const deleteMutationNoticeKind =
    deleteMutationApiError != null ? errorToNoticeKind(deleteMutationApiError) : null;

  const isFormValid =
    form.symbol.trim().length > 0 &&
    Number.isFinite(Number(form.buyPrice)) &&
    Number(form.buyPrice) > 0 &&
    Number.isFinite(Number(form.sellPrice)) &&
    Number(form.sellPrice) > 0 &&
    Number.isFinite(Number(form.shares)) &&
    Number(form.shares) > 0 &&
    form.sellDate.length > 0;

  const resetForm = () => {
    setForm(EMPTY_REALIZED_GAINS_FORM);
    setEditingTradeId(null);
  };

  const beginEdit = (trade: RealizedTrade) => {
    setEditingTradeId(trade.id);
    setForm({
      symbol: trade.symbol,
      buyPrice: String(trade.buyPrice),
      sellPrice: String(trade.sellPrice),
      shares: String(trade.shares),
      buyDate: trade.buyDate ?? "",
      sellDate: trade.sellDate,
    });
  };

  const submitForm = () => {
    realized.createMutation.reset();
    realized.updateMutation.reset();
    const payload = {
      symbol: form.symbol.trim(),
      currency,
      buyPrice: Number(form.buyPrice),
      sellPrice: Number(form.sellPrice),
      shares: Number(form.shares),
      buyDate: form.buyDate || null,
      sellDate: form.sellDate,
    };
    if (editingTradeId == null) {
      realized.createMutation.mutate(payload, { onSuccess: resetForm });
      return;
    }
    realized.updateMutation.mutate(
      { itemId: editingTradeId, payload },
      { onSuccess: resetForm },
    );
  };

  const switchCurrency = (nextCurrency: RealizedTradeCurrency) => {
    if (nextCurrency === currency) {
      return;
    }
    setCurrency(nextCurrency);
    resetForm();
    realized.createMutation.reset();
    realized.updateMutation.reset();
    realized.deleteMutation.reset();
    setPendingDeleteId(null);
  };

  const closeDeleteModal = () => {
    if (!realized.deleteMutation.isPending) {
      setPendingDeleteId(null);
    }
  };

  const confirmDelete = () => {
    const tradeId = pendingDeleteIdRef.current;
    if (tradeId == null) {
      return;
    }
    realized.deleteMutation.mutate(tradeId, {
      onSuccess: () => {
        pendingDeleteIdRef.current = null;
        setPendingDeleteId(null);
        if (editingTradeId === tradeId) {
          resetForm();
        }
      },
    });
  };

  const openDeleteModal = (tradeId: number) => {
    realized.deleteMutation.reset();
    pendingDeleteIdRef.current = tradeId;
    setPendingDeleteId(tradeId);
  };

  return {
    currency,
    realized,
    form,
    setForm,
    editingTradeId,
    pendingDeleteId,
    pendingDeleteIdRef,
    today,
    preview,
    monthlySeries,
    maxAbsMonthlyGain,
    noticeKind,
    formMutationApiError,
    formMutationNoticeKind,
    deleteMutationApiError,
    deleteMutationNoticeKind,
    isFormValid,
    resetForm,
    beginEdit,
    submitForm,
    switchCurrency,
    closeDeleteModal,
    confirmDelete,
    openDeleteModal,
  };
}
