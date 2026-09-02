"use client";

import {
  Button,
  Column,
  ComposedModal,
  Grid,
  InlineLoading,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
  Tile,
} from "@carbon/react";
import type { RealizedTrade, RealizedTradeCurrency } from "@shared/api";
import { useMemo, useRef, useState } from "react";
import { useLanguage } from "../../app/i18n";
import { useRealizedTrades } from "../../hooks/useRealizedTrades";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { toApiError } from "../../services/api/client";
import {
  buildMonthlyRealizedGainSeries,
  previewRealizedGain,
  sumRealizedGainForPeriod,
  type RealizedGainPeriod,
} from "./realizedGainCalculations";

const PERIODS: RealizedGainPeriod[] = ["1M", "3M", "6M", "1Y"];

const CURRENCIES: RealizedTradeCurrency[] = ["TWD", "USD"];

type FormState = {
  symbol: string;
  buyPrice: string;
  sellPrice: string;
  shares: string;
  buyDate: string;
  sellDate: string;
};

const EMPTY_FORM: FormState = {
  symbol: "",
  buyPrice: "",
  sellPrice: "",
  shares: "",
  buyDate: "",
  sellDate: "",
};

function readInputValue(event: { currentTarget: HTMLInputElement | null; target: EventTarget | null }) {
  const target = event.currentTarget ?? event.target;
  return target instanceof HTMLInputElement ? target.value : "";
}

export function RealizedGainsPage() {
  const { t } = useLanguage();
  const [currency, setCurrency] = useState<RealizedTradeCurrency>("TWD");
  const realized = useRealizedTrades(currency);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
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

  const noticeKind =
    realized.tradesQuery.error != null
      ? realized.trades.length > 0
        ? "stale"
        : errorToNoticeKind(realized.tradesQuery.error)
      : null;
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
    setForm(EMPTY_FORM);
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
      { tradeId: editingTradeId, payload },
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

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-hero-tile">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("realizedGains.title")}</h1>
            <p className="app-hero-description app-body-copy">{t("realizedGains.description")}</p>
          </div>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("realizedGains.currency.label")}</p>
              <h2>{t("realizedGains.currency.label")}</h2>
            </div>
            <div
              className="app-realized-currency-switch"
              role="group"
              aria-label={t("realizedGains.currency.label")}
            >
              {CURRENCIES.map((value) => (
                <Button
                  key={value}
                  kind={currency === value ? "primary" : "tertiary"}
                  size="sm"
                  data-testid={`realized-currency-${value.toLowerCase()}`}
                  onClick={() => switchCurrency(value)}
                >
                  {value === "TWD" ? t("realizedGains.currency.twd") : t("realizedGains.currency.usd")}
                </Button>
              ))}
            </div>
          </div>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <div className="app-realized-summary-grid">
          {PERIODS.map((period) => (
            <Tile key={period} className="app-realized-summary-card">
              <p className="app-eyebrow">{t(`realizedGains.period.${period}`)}</p>
              <p className="app-realized-summary-value">
                {formatSignedCurrency(
                  sumRealizedGainForPeriod(realized.trades, period, today),
                  currency,
                )}
              </p>
            </Tile>
          ))}
        </div>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("realizedGains.chart.title")}</p>
              <h2>{t("realizedGains.chart.title")}</h2>
            </div>
          </div>
          <div className="app-realized-chart" role="img" aria-label={t("realizedGains.chart.title")}>
            {monthlySeries.map((point) => {
              const heightPercent =
                point.totalGain === 0
                  ? 0
                  : (Math.abs(point.totalGain) / maxAbsMonthlyGain) * 50;
              const isNegative = point.totalGain < 0;
              return (
                <div key={point.monthKey} className="app-realized-chart-column">
                  <div className="app-realized-chart-axis">
                    <div className="app-realized-chart-half app-realized-chart-half-positive">
                      {!isNegative && point.totalGain !== 0 ? (
                        <div
                          className="app-realized-chart-bar app-realized-chart-bar-positive"
                          style={{ height: `${heightPercent}%` }}
                          title={`${point.label}: ${formatSignedCurrency(point.totalGain, currency)}`}
                        />
                      ) : null}
                    </div>
                    <div className="app-realized-chart-zero-line" aria-hidden="true" />
                    <div className="app-realized-chart-half app-realized-chart-half-negative">
                      {isNegative ? (
                        <div
                          className="app-realized-chart-bar app-realized-chart-bar-negative"
                          style={{ height: `${heightPercent}%` }}
                          title={`${point.label}: ${formatSignedCurrency(point.totalGain, currency)}`}
                        />
                      ) : null}
                    </div>
                  </div>
                  <span className="app-realized-chart-label">{point.label.slice(5)}</span>
                </div>
              );
            })}
          </div>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("realizedGains.form.title")}</p>
              <h2>{editingTradeId == null ? t("realizedGains.form.createTitle") : t("realizedGains.form.editTitle")}</h2>
            </div>
          </div>
          {formMutationNoticeKind ? (
            <div className="app-state-notice-wrap">
              <RequestStateNotice
                kind={formMutationNoticeKind}
                scopeLabel={t("realizedGains.form.title")}
                detail={
                  formMutationNoticeKind === "validation"
                    ? formMutationApiError?.detail
                    : null
                }
              />
            </div>
          ) : null}
          <div className="app-realized-form">
            <TextInput
              id="realized-symbol"
              labelText={t("realizedGains.form.symbol")}
              value={form.symbol}
              onChange={(event) =>
                setForm((current) => ({ ...current, symbol: readInputValue(event) }))
              }
            />
            <TextInput
              id="realized-buy-price"
              labelText={t("realizedGains.form.buyPrice")}
              type="number"
              min={0}
              step="any"
              value={form.buyPrice}
              onChange={(event) =>
                setForm((current) => ({ ...current, buyPrice: readInputValue(event) }))
              }
            />
            <TextInput
              id="realized-sell-price"
              labelText={t("realizedGains.form.sellPrice")}
              type="number"
              min={0}
              step="any"
              value={form.sellPrice}
              onChange={(event) =>
                setForm((current) => ({ ...current, sellPrice: readInputValue(event) }))
              }
            />
            <TextInput
              id="realized-shares"
              labelText={t("realizedGains.form.shares")}
              type="number"
              min={0}
              step="any"
              value={form.shares}
              onChange={(event) =>
                setForm((current) => ({ ...current, shares: readInputValue(event) }))
              }
            />
            <TextInput
              id="realized-buy-date"
              labelText={t("realizedGains.form.buyDate")}
              type="date"
              value={form.buyDate}
              onChange={(event) =>
                setForm((current) => ({ ...current, buyDate: readInputValue(event) }))
              }
            />
            <TextInput
              id="realized-sell-date"
              labelText={t("realizedGains.form.sellDate")}
              type="date"
              value={form.sellDate}
              onChange={(event) =>
                setForm((current) => ({ ...current, sellDate: readInputValue(event) }))
              }
            />
            {preview ? (
              <div className="app-realized-preview">
                <p>{t("realizedGains.preview.gain")}</p>
                <p>{formatSignedCurrency(preview.realizedGain, currency)}</p>
                <p>{t("realizedGains.preview.return")}</p>
                <p>{formatSignedPercent(preview.realizedReturnPercent)}</p>
              </div>
            ) : null}
            <div className="app-realized-form-actions">
              <Button
                data-testid="realized-submit-button"
                disabled={!isFormValid || realized.isMutating}
                onClick={submitForm}
              >
                {editingTradeId == null ? t("realizedGains.form.submitCreate") : t("realizedGains.form.submitUpdate")}
              </Button>
              {editingTradeId != null ? (
                <Button kind="ghost" onClick={resetForm}>
                  {t("realizedGains.form.cancel")}
                </Button>
              ) : null}
            </div>
          </div>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("realizedGains.history.title")}</p>
              <h2>{t("realizedGains.history.title")}</h2>
            </div>
          </div>
          {noticeKind ? (
            <div className="app-state-notice-wrap">
              <RequestStateNotice kind={noticeKind} scopeLabel={t("realizedGains.history.title")} />
            </div>
          ) : null}
          {realized.tradesQuery.isLoading && realized.trades.length === 0 ? (
            <InlineLoading description={t("realizedGains.loading")} status="active" />
          ) : realized.trades.length === 0 ? (
            <p className="app-supporting-copy">
              {currency === "TWD"
                ? t("realizedGains.empty.twd")
                : t("realizedGains.empty.usd")}
            </p>
          ) : (
            <TableContainer className="app-realized-table-wrap">
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("realizedGains.column.sellDate")}</TableHeader>
                    <TableHeader>{t("realizedGains.column.symbol")}</TableHeader>
                    <TableHeader>{t("realizedGains.column.buyPrice")}</TableHeader>
                    <TableHeader>{t("realizedGains.column.sellPrice")}</TableHeader>
                    <TableHeader>{t("realizedGains.column.shares")}</TableHeader>
                    <TableHeader>{t("realizedGains.column.returnPercent")}</TableHeader>
                    <TableHeader>{t("realizedGains.column.realizedGain")}</TableHeader>
                    <TableHeader>{t("realizedGains.column.actions")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {realized.trades.map((trade) => (
                    <TableRow key={trade.id} data-testid={`realized-trade-row-${trade.id}`}>
                      <TableCell>{trade.sellDate}</TableCell>
                      <TableCell>{trade.symbol}</TableCell>
                      <TableCell>{formatCurrency(trade.buyPrice, currency)}</TableCell>
                      <TableCell>{formatCurrency(trade.sellPrice, currency)}</TableCell>
                      <TableCell>{trade.shares}</TableCell>
                      <TableCell>{formatSignedPercent(trade.realizedReturnPercent)}</TableCell>
                      <TableCell>{formatSignedCurrency(trade.realizedGain, currency)}</TableCell>
                      <TableCell>
                        <div className="app-realized-actions">
                          <Button kind="ghost" size="sm" onClick={() => beginEdit(trade)}>
                            {t("realizedGains.actions.edit")}
                          </Button>
                          <Button
                            kind="ghost"
                            size="sm"
                            data-testid={`realized-delete-button-${trade.id}`}
                            onClick={() => {
                              realized.deleteMutation.reset();
                              pendingDeleteIdRef.current = trade.id;
                              setPendingDeleteId(trade.id);
                            }}
                          >
                            {t("realizedGains.actions.delete")}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Tile>
      </Column>

      {pendingDeleteId != null ? (
        <ComposedModal
          open
          onClose={() => {
            if (!realized.deleteMutation.isPending) {
              setPendingDeleteId(null);
            }
          }}
          size="sm"
          danger
        >
          <ModalHeader
            title={t("realizedGains.deleteConfirmTitle")}
            closeModal={() => {
              if (!realized.deleteMutation.isPending) {
                setPendingDeleteId(null);
              }
            }}
          />
          <ModalBody>
            <p className="app-supporting-copy">{t("realizedGains.deleteConfirmMessage")}</p>
            {deleteMutationNoticeKind ? (
              <div className="app-state-notice-wrap">
                <RequestStateNotice
                  kind={deleteMutationNoticeKind}
                  scopeLabel={t("realizedGains.deleteConfirmTitle")}
                  detail={
                    deleteMutationNoticeKind === "validation"
                      ? deleteMutationApiError?.detail
                      : null
                  }
                />
              </div>
            ) : null}
          </ModalBody>
          <ModalFooter>
            <Button
              kind="secondary"
              disabled={realized.deleteMutation.isPending}
              onClick={() => setPendingDeleteId(null)}
            >
              {t("realizedGains.deleteConfirmCancel")}
            </Button>
            <Button
              kind="danger"
              data-testid="realized-delete-confirm"
              disabled={realized.deleteMutation.isPending}
              onClick={() => {
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
              }}
            >
              {t("realizedGains.deleteConfirmAction")}
            </Button>
          </ModalFooter>
        </ComposedModal>
      ) : null}
    </Grid>
  );
}

function formatCurrency(value: number, currency: RealizedTradeCurrency) {
  const prefix = currency === "TWD" ? "NT$" : "US$";
  return `${prefix}${value.toLocaleString(undefined, { maximumFractionDigits: 6 })}`;
}

function formatSignedCurrency(value: number, currency: RealizedTradeCurrency) {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${formatCurrency(Math.abs(value), currency)}`;
}

function formatSignedPercent(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}
