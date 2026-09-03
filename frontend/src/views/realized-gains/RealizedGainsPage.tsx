"use client";

import "./realizedGains.css";
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
import { useLanguage, type TranslationKey } from "../../app/i18n";
import { RealizedGainsPageSection, RealizedGainsSectionHeader, ScopedRequestStateNotice } from "./RealizedGainsSection";
import {
  sumRealizedGainForPeriod,
  type RealizedGainPeriod,
} from "./realizedGainCalculations";
import { RealizedGainChartMonth } from "./RealizedGainChartMonth";
import {
  formatRealizedCurrency,
  formatSignedRealizedCurrency,
  formatSignedRealizedPercent,
  realizedCurrencyPrefix,
} from "./realizedGainFormat";
import {
  type RealizedGainsFormState,
  useRealizedGainsPageState,
} from "./useRealizedGainsPageState";

const PERIODS: RealizedGainPeriod[] = ["1M", "3M", "6M", "1Y"];

const CURRENCIES: RealizedTradeCurrency[] = ["TWD", "USD"];

const FORM_FIELDS: ReadonlyArray<{
  id: string;
  field: keyof RealizedGainsFormState;
  labelKey: TranslationKey;
  type?: string;
  min?: number;
  step?: string;
}> = [
  { id: "realized-symbol", field: "symbol", labelKey: "realizedGains.form.symbol" },
  {
    id: "realized-buy-price",
    field: "buyPrice",
    labelKey: "realizedGains.form.buyPrice",
    type: "number",
    min: 0,
    step: "any",
  },
  {
    id: "realized-sell-price",
    field: "sellPrice",
    labelKey: "realizedGains.form.sellPrice",
    type: "number",
    min: 0,
    step: "any",
  },
  {
    id: "realized-shares",
    field: "shares",
    labelKey: "realizedGains.form.shares",
    type: "number",
    min: 0,
    step: "any",
  },
  {
    id: "realized-cumulative-dividends",
    field: "cumulativeDividends",
    labelKey: "realizedGains.form.cumulativeDividends",
    type: "number",
    min: 0,
    step: "any",
  },
  {
    id: "realized-buy-date",
    field: "buyDate",
    labelKey: "realizedGains.form.buyDate",
    type: "date",
  },
  {
    id: "realized-sell-date",
    field: "sellDate",
    labelKey: "realizedGains.form.sellDate",
    type: "date",
  },
];

function readInputValue(event: { currentTarget: HTMLInputElement | null; target: EventTarget | null }) {
  const target = event.currentTarget ?? event.target;
  return target instanceof HTMLInputElement ? target.value : "";
}

type RealizedGainsHistoryProps = Readonly<{
  currency: RealizedTradeCurrency;
  trades: RealizedTrade[];
  isLoading: boolean;
  onEdit: (trade: RealizedTrade) => void;
  onDelete: (tradeId: number) => void;
}>;

function RealizedGainsHistory({
  currency,
  trades,
  isLoading,
  onEdit,
  onDelete,
}: RealizedGainsHistoryProps) {
  const { t } = useLanguage();

  if (isLoading && trades.length === 0) {
    return <InlineLoading description={t("realizedGains.loading")} status="active" />;
  }
  if (trades.length === 0) {
    const emptyKey = currency === "TWD" ? "realizedGains.empty.twd" : "realizedGains.empty.usd";
    return <p className="app-supporting-copy">{t(emptyKey)}</p>;
  }

  return (
    <TableContainer className="app-realized-table-wrap">
      <Table size="lg" useZebraStyles>
        <TableHead>
          <TableRow>
            <TableHeader>{t("realizedGains.column.sellDate")}</TableHeader>
            <TableHeader>{t("realizedGains.column.symbol")}</TableHeader>
            <TableHeader>{t("realizedGains.column.buyPrice")}</TableHeader>
            <TableHeader>{t("realizedGains.column.sellPrice")}</TableHeader>
            <TableHeader>{t("realizedGains.column.shares")}</TableHeader>
            <TableHeader>{t("realizedGains.column.dividends")}</TableHeader>
            <TableHeader>{t("realizedGains.column.returnPercent")}</TableHeader>
            <TableHeader>{t("realizedGains.column.realizedGain")}</TableHeader>
            <TableHeader>{t("realizedGains.column.actions")}</TableHeader>
          </TableRow>
        </TableHead>
        <TableBody>
          {trades.map((trade) => (
            <TableRow key={trade.id} data-testid={`realized-trade-row-${trade.id}`}>
              <TableCell>{trade.sellDate}</TableCell>
              <TableCell>{trade.symbol}</TableCell>
              <TableCell>{formatRealizedCurrency(trade.buyPrice, currency)}</TableCell>
              <TableCell>{formatRealizedCurrency(trade.sellPrice, currency)}</TableCell>
              <TableCell>{trade.shares}</TableCell>
              <TableCell>{formatRealizedCurrency(trade.cumulativeDividends, currency)}</TableCell>
              <TableCell>{formatSignedRealizedPercent(trade.realizedReturnPercent)}</TableCell>
              <TableCell>{formatSignedRealizedCurrency(trade.realizedGain, currency)}</TableCell>
              <TableCell>
                <div className="app-alerts-actions">
                  <Button kind="ghost" size="sm" onClick={() => onEdit(trade)}>
                    {t("realizedGains.actions.edit")}
                  </Button>
                  <Button
                    kind="ghost"
                    size="sm"
                    data-testid={`realized-delete-button-${trade.id}`}
                    onClick={() => onDelete(trade.id)}
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
  );
}

export function RealizedGainsPage() {
  const { t } = useLanguage();
  const {
    currency,
    realized,
    form,
    setForm,
    editingTradeId,
    pendingDeleteId,
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
  } = useRealizedGainsPageState();

  const formTitleKey =
    editingTradeId == null ? "realizedGains.form.createTitle" : "realizedGains.form.editTitle";
  const submitLabelKey =
    editingTradeId == null ? "realizedGains.form.submitCreate" : "realizedGains.form.submitUpdate";

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

      <RealizedGainsPageSection className="app-section-tile">
        <RealizedGainsSectionHeader
          eyebrow={t("realizedGains.currency.label")}
          title={t("realizedGains.currency.label")}
          actions={
            <fieldset className="app-realized-currency-switch">
              <legend className="cds--visually-hidden">{t("realizedGains.currency.label")}</legend>
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
            </fieldset>
          }
        />
      </RealizedGainsPageSection>

      <RealizedGainsPageSection>
        <div className="app-realized-summary-grid">
          {PERIODS.map((period) => (
            <Tile key={period} className="app-realized-summary-card">
              <p className="app-eyebrow">{t(`realizedGains.period.${period}`)}</p>
              <p className="app-realized-summary-value">
                {formatSignedRealizedCurrency(
                  sumRealizedGainForPeriod(realized.trades, period, today),
                  currency,
                )}
              </p>
            </Tile>
          ))}
        </div>
      </RealizedGainsPageSection>

      <RealizedGainsPageSection className="app-section-tile">
        <RealizedGainsSectionHeader
          eyebrow={t("realizedGains.chart.title")}
          title={t("realizedGains.chart.title")}
        />
        <div className="app-realized-chart" role="img" aria-label={t("realizedGains.chart.title")}>
          {monthlySeries.map((point) => (
            <RealizedGainChartMonth
              key={point.monthKey}
              point={point}
              currency={currency}
              maxAbsMonthlyGain={maxAbsMonthlyGain}
            />
          ))}
        </div>
      </RealizedGainsPageSection>

      <RealizedGainsPageSection className="app-section-tile">
        <RealizedGainsSectionHeader
          eyebrow={t("realizedGains.form.title")}
          title={t(formTitleKey)}
        />
        <ScopedRequestStateNotice
          kind={formMutationNoticeKind}
          scopeLabel={t("realizedGains.form.title")}
          detail={formMutationApiError?.detail}
        />
        <div className="app-alerts-form">
          {FORM_FIELDS.map(({ id, field, labelKey, type, min, step }) => (
            <TextInput
              key={id}
              id={id}
              labelText={
                field === "cumulativeDividends"
                  ? `${t(labelKey)} (${realizedCurrencyPrefix(currency)})`
                  : t(labelKey)
              }
              helperText={
                field === "cumulativeDividends"
                  ? t("realizedGains.form.cumulativeDividendsHelper")
                  : undefined
              }
              type={type}
              min={min}
              step={step}
              value={form[field]}
              onChange={(event) =>
                setForm((current) => ({ ...current, [field]: readInputValue(event) }))
              }
            />
          ))}
          {preview ? (
            <div className="app-realized-preview">
              <p>{t("realizedGains.preview.priceGain")}</p>
              <p>{formatSignedRealizedCurrency(preview.priceGain, currency)}</p>
              <p>{t("realizedGains.preview.dividends")}</p>
              <p>{formatSignedRealizedCurrency(preview.cumulativeDividends, currency)}</p>
              <p>{t("realizedGains.preview.gain")}</p>
              <p>{formatSignedRealizedCurrency(preview.realizedGain, currency)}</p>
              <p>{t("realizedGains.preview.return")}</p>
              <p>{formatSignedRealizedPercent(preview.realizedReturnPercent)}</p>
            </div>
          ) : null}
          <div className="app-alerts-form-actions">
            <Button
              data-testid="realized-submit-button"
              disabled={!isFormValid || realized.isMutating}
              onClick={submitForm}
            >
              {t(submitLabelKey)}
            </Button>
            {editingTradeId != null ? (
              <Button kind="ghost" onClick={resetForm}>
                {t("realizedGains.form.cancel")}
              </Button>
            ) : null}
          </div>
        </div>
      </RealizedGainsPageSection>

      <RealizedGainsPageSection className="app-section-tile">
        <RealizedGainsSectionHeader
          eyebrow={t("realizedGains.history.title")}
          title={t("realizedGains.history.title")}
        />
        {noticeKind ? (
          <ScopedRequestStateNotice
            kind={noticeKind}
            scopeLabel={t("realizedGains.history.title")}
          />
        ) : null}
        <RealizedGainsHistory
          currency={currency}
          trades={realized.trades}
          isLoading={realized.tradesQuery.isLoading}
          onEdit={beginEdit}
          onDelete={openDeleteModal}
        />
      </RealizedGainsPageSection>

      {pendingDeleteId != null ? (
        <ComposedModal open onClose={closeDeleteModal} size="sm" danger>
          <ModalHeader title={t("realizedGains.deleteConfirmTitle")} closeModal={closeDeleteModal} />
          <ModalBody>
            <p className="app-supporting-copy">{t("realizedGains.deleteConfirmMessage")}</p>
            <ScopedRequestStateNotice
              kind={deleteMutationNoticeKind}
              scopeLabel={t("realizedGains.deleteConfirmTitle")}
              detail={deleteMutationApiError?.detail}
            />
          </ModalBody>
          <ModalFooter>
            <Button
              kind="secondary"
              disabled={realized.deleteMutation.isPending}
              onClick={closeDeleteModal}
            >
              {t("realizedGains.deleteConfirmCancel")}
            </Button>
            <Button
              kind="danger"
              data-testid="realized-delete-confirm"
              disabled={realized.deleteMutation.isPending}
              onClick={confirmDelete}
            >
              {t("realizedGains.deleteConfirmAction")}
            </Button>
          </ModalFooter>
        </ComposedModal>
      ) : null}
    </Grid>
  );
}
