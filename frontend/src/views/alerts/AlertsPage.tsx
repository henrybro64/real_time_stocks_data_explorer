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
  Select,
  SelectItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
  TextInput,
  Tile,
} from "@carbon/react";
import type { AlertRule, AlertRuleType } from "@shared/api";
import Link from "next/link";
import { useState } from "react";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { useAlerts } from "../../hooks/useAlerts";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { toApiError } from "../../services/api/client";
import { formatCurrency, formatLastUpdated } from "../../services/format/quote";
import {
  AlertStockResolutionError,
  resolveAlertStockId,
} from "./resolveAlertStockId";
import {
  formatAlertStockResolutionMessage,
  isAlertStockValidationDetail,
  localizeAlertStockApiDetail,
} from "./alertStockValidationCopy";

type Props = {
  initialStockId?: string;
};

export function AlertsPage({ initialStockId = "" }: Props) {
  const { language, t } = useLanguage();
  const alerts = useAlerts();
  const [editingRuleId, setEditingRuleId] = useState<number | null>(null);
  const [draftStockId, setDraftStockId] = useState(initialStockId);
  const [draftRuleType, setDraftRuleType] = useState<AlertRuleType>("price_above");
  const [draftThresholdValue, setDraftThresholdValue] = useState("");
  const [draftEnabled, setDraftEnabled] = useState(true);
  const [stockFieldError, setStockFieldError] = useState<string | null>(null);
  const [isResolvingStock, setIsResolvingStock] = useState(false);
  const [clearHistoryOpen, setClearHistoryOpen] = useState(false);

  const rulesLoading = alerts.rulesQuery.isLoading && alerts.rules.length === 0;
  const eventsLoading = alerts.eventsQuery.isLoading && alerts.events.length === 0;
  const rulesNoticeKind =
    alerts.rulesQuery.error != null
      ? (alerts.rules.length > 0 ? "stale" : errorToNoticeKind(alerts.rulesQuery.error))
      : null;
  const eventsNoticeKind =
    alerts.eventsQuery.error != null
      ? (alerts.events.length > 0 ? "stale" : errorToNoticeKind(alerts.eventsQuery.error))
      : null;
  const mutationApiError = alerts.mutationError != null ? toApiError(alerts.mutationError) : null;
  // Suppress the save banner only when a form submit already attached a field error.
  // Row actions (pause/resume/delete) keep the banner even for stock validation details.
  const mutationNoticeKind =
    mutationApiError != null &&
    !(stockFieldError != null && isAlertStockValidationDetail(mutationApiError.detail))
      ? errorToNoticeKind(mutationApiError)
      : null;
  const isSubmitDisabled =
    alerts.isMutating ||
    isResolvingStock ||
    draftStockId.trim().length === 0 ||
    !Number.isFinite(Number(draftThresholdValue)) ||
    Number(draftThresholdValue) <= 0;

  const resetForm = () => {
    setEditingRuleId(null);
    setDraftStockId(initialStockId);
    setDraftRuleType("price_above");
    setDraftThresholdValue("");
    setDraftEnabled(true);
    setStockFieldError(null);
  };

  const beginEdit = (rule: AlertRule) => {
    setEditingRuleId(rule.id);
    setDraftStockId(rule.stockId);
    setDraftRuleType(rule.ruleType);
    setDraftThresholdValue(String(rule.thresholdValue));
    setDraftEnabled(rule.enabled);
    setStockFieldError(null);
  };

  const applyMutationStockError = (error: unknown) => {
    const apiError = toApiError(error);
    if (apiError.kind !== "validation" || !apiError.detail) {
      return;
    }
    if (isAlertStockValidationDetail(apiError.detail)) {
      setStockFieldError(localizeAlertStockApiDetail(apiError.detail, t));
    }
  };

  const submitForm = async () => {
    setStockFieldError(null);
    alerts.createMutation.reset();
    alerts.updateMutation.reset();
    alerts.deleteMutation.reset();

    let resolvedStockId: string;
    try {
      setIsResolvingStock(true);
      resolvedStockId = await resolveAlertStockId(draftStockId);
    } catch (error) {
      const message =
        error instanceof AlertStockResolutionError
          ? formatAlertStockResolutionMessage(error, t)
          : t("alerts.error.unknownStock");
      setStockFieldError(message);
      return;
    } finally {
      setIsResolvingStock(false);
    }

    setDraftStockId(resolvedStockId);
    const payload = {
      stockId: resolvedStockId,
      ruleType: draftRuleType,
      thresholdValue: Number(draftThresholdValue),
      enabled: draftEnabled,
    };

    if (editingRuleId == null) {
      alerts.createMutation.mutate(payload, {
        onSuccess: () => {
          resetForm();
        },
        onError: applyMutationStockError,
      });
      return;
    }

    alerts.updateMutation.mutate(
      {
        ruleId: editingRuleId,
        payload,
      },
      {
        onSuccess: () => {
          resetForm();
        },
        onError: applyMutationStockError,
      },
    );
  };

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-hero-tile">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("alerts.title")}</h1>
            <p className="app-body-copy app-hero-description">{t("alerts.description")}</p>
          </div>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("alerts.form.eyebrow")}</p>
              <h2>{editingRuleId == null ? t("alerts.form.createTitle") : t("alerts.form.editTitle")}</h2>
            </div>
          </div>
          {mutationNoticeKind ? (
            <div className="app-state-notice-wrap">
              <RequestStateNotice
                kind={mutationNoticeKind}
                scopeLabel={t("alerts.form.createTitle")}
                detail={
                  mutationNoticeKind === "validation" ? mutationApiError?.detail : null
                }
              />
            </div>
          ) : null}
          <div className="app-alerts-form">
            <TextInput
              id="alert-stock-id"
              labelText={t("alerts.form.stockId")}
              helperText={t("alerts.form.stockIdHelper")}
              placeholder={t("alerts.form.stockIdPlaceholder")}
              value={draftStockId}
              invalid={stockFieldError != null}
              invalidText={stockFieldError ?? undefined}
              onChange={(event) => {
                setDraftStockId(event.currentTarget.value);
                setStockFieldError(null);
              }}
            />
            <Select
              id="alert-rule-type"
              labelText={t("alerts.form.ruleType")}
              value={draftRuleType}
              onChange={(event) => setDraftRuleType(event.currentTarget.value as AlertRuleType)}
            >
              <SelectItem value="price_above" text={t("alerts.ruleType.priceAbove")} />
              <SelectItem value="price_below" text={t("alerts.ruleType.priceBelow")} />
            </Select>
            <TextInput
              id="alert-threshold-value"
              labelText={t("alerts.form.threshold")}
              type="number"
              min={0}
              step="any"
              value={draftThresholdValue}
              onChange={(event) => setDraftThresholdValue(event.currentTarget.value)}
            />
            <Select
              id="alert-enabled"
              labelText={t("alerts.form.enabled")}
              value={draftEnabled ? "enabled" : "disabled"}
              onChange={(event) => setDraftEnabled(event.currentTarget.value === "enabled")}
            >
              <SelectItem value="enabled" text={t("alerts.status.enabled")} />
              <SelectItem value="disabled" text={t("alerts.status.disabled")} />
            </Select>
            <div className="app-alerts-form-actions">
              <Button
                data-testid="alert-submit-button"
                onClick={() => {
                  void submitForm();
                }}
                disabled={isSubmitDisabled}
              >
                {editingRuleId == null ? t("alerts.form.submitCreate") : t("alerts.form.submitUpdate")}
              </Button>
              {editingRuleId != null ? (
                <Button kind="ghost" onClick={resetForm}>
                  {t("alerts.form.cancelEdit")}
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
              <p className="app-eyebrow">{t("alerts.rules.title")}</p>
              <h2>{t("alerts.rules.title")}</h2>
            </div>
          </div>
          {rulesNoticeKind ? (
            <div className="app-state-notice-wrap">
              <RequestStateNotice kind={rulesNoticeKind} scopeLabel={t("alerts.rules.title")} />
            </div>
          ) : null}
          {rulesLoading ? (
            <InlineLoading description={t("home.summary.loading")} status="active" />
          ) : alerts.rules.length === 0 ? (
            <p className="app-supporting-copy">{t("alerts.rules.empty")}</p>
          ) : (
            <TableContainer className="app-alerts-table-wrap">
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("alerts.column.stock")}</TableHeader>
                    <TableHeader>{t("alerts.column.rule")}</TableHeader>
                    <TableHeader>{t("alerts.column.threshold")}</TableHeader>
                    <TableHeader>{t("alerts.column.status")}</TableHeader>
                    <TableHeader>{t("alerts.column.lastObserved")}</TableHeader>
                    <TableHeader>{t("alerts.column.lastTriggered")}</TableHeader>
                    <TableHeader>{t("alerts.column.actions")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {alerts.rules.map((rule) => (
                    <TableRow key={rule.id} data-testid={`alerts-rule-row-${rule.id}`}>
                      <TableCell>
                        <div className="app-alerts-stock-cell">
                          <Link href={`/stock/${encodeURIComponent(rule.stockId)}`}>
                            {rule.stock?.symbol ?? rule.stockId}
                          </Link>
                          <span>{rule.stock ? getDisplayStockName(rule.stock, language) : rule.stockId}</span>
                        </div>
                      </TableCell>
                      <TableCell>{t(ruleTypeLabel(rule.ruleType))}</TableCell>
                      <TableCell>{formatPrice(rule.thresholdValue, rule.stock?.currency)}</TableCell>
                      <TableCell>
                        <div className="app-alerts-tag-row">
                          <Tag type={rule.enabled ? "green" : "cool-gray"}>
                            {rule.enabled ? t("alerts.status.enabled") : t("alerts.status.disabled")}
                          </Tag>
                          <Tag type={rule.lastConditionMet ? "magenta" : "teal"}>
                            {rule.lastConditionMet ? t("alerts.status.conditionMet") : t("alerts.status.monitoring")}
                          </Tag>
                        </div>
                      </TableCell>
                      <TableCell>
                        {rule.lastObservedPrice == null
                          ? "—"
                          : formatPrice(rule.lastObservedPrice, rule.stock?.currency)}
                      </TableCell>
                      <TableCell>{rule.lastTriggeredAt ? formatLastUpdated(rule.lastTriggeredAt) : "—"}</TableCell>
                      <TableCell>
                        <div className="app-alerts-actions">
                          <Button kind="ghost" size="sm" onClick={() => beginEdit(rule)}>
                            {t("alerts.actions.edit")}
                          </Button>
                          <Button
                            kind="ghost"
                            size="sm"
                            onClick={() => {
                              setStockFieldError(null);
                              alerts.updateMutation.mutate({
                                ruleId: rule.id,
                                payload: { enabled: !rule.enabled },
                              });
                            }}
                          >
                            {rule.enabled ? t("alerts.actions.pause") : t("alerts.actions.resume")}
                          </Button>
                          <Button
                            kind="ghost"
                            size="sm"
                            onClick={() => {
                              setStockFieldError(null);
                              alerts.deleteMutation.mutate(rule.id, {
                                onSuccess: () => {
                                  if (editingRuleId === rule.id) {
                                    resetForm();
                                  }
                                },
                              });
                            }}
                          >
                            {t("alerts.actions.delete")}
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

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("alerts.events.title")}</p>
              <h2>{t("alerts.events.title")}</h2>
            </div>
            <Button
              kind="danger--ghost"
              size="sm"
              data-testid="alerts-clear-history-button"
              disabled={alerts.isClearingEvents}
              onClick={() => setClearHistoryOpen(true)}
            >
              {t("alerts.events.clearHistory")}
            </Button>
          </div>
          {eventsNoticeKind ? (
            <div className="app-state-notice-wrap">
              <RequestStateNotice kind={eventsNoticeKind} scopeLabel={t("alerts.events.title")} />
            </div>
          ) : null}
          {eventsLoading ? (
            <InlineLoading description={t("home.summary.loading")} status="active" />
          ) : alerts.events.length === 0 ? (
            <p className="app-supporting-copy">{t("alerts.events.empty")}</p>
          ) : (
            <TableContainer className="app-alerts-table-wrap">
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("alerts.column.stock")}</TableHeader>
                    <TableHeader>{t("alerts.column.rule")}</TableHeader>
                    <TableHeader>{t("alerts.column.observedPrice")}</TableHeader>
                    <TableHeader>{t("alerts.column.triggeredAt")}</TableHeader>
                    <TableHeader>{t("alerts.column.message")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {alerts.events.map((event) => (
                    <TableRow key={event.id} data-testid={`alerts-event-row-${event.id}`}>
                      <TableCell>
                        <div className="app-alerts-stock-cell">
                          <Link href={`/stock/${encodeURIComponent(event.stockId)}`}>
                            {event.stock?.symbol ?? event.stockId}
                          </Link>
                          <span>{event.stock ? getDisplayStockName(event.stock, language) : event.stockId}</span>
                        </div>
                      </TableCell>
                      <TableCell>
                        {t(ruleTypeLabel(event.ruleType))} {formatPrice(event.thresholdValue, event.stock?.currency)}
                      </TableCell>
                      <TableCell>{formatPrice(event.observedPrice, event.stock?.currency)}</TableCell>
                      <TableCell>{formatLastUpdated(event.triggeredAt)}</TableCell>
                      <TableCell>{event.message}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Tile>
      </Column>

      {clearHistoryOpen ? (
        <ComposedModal
          open
          onClose={() => {
            if (!alerts.isClearingEvents) {
              setClearHistoryOpen(false);
            }
          }}
          size="sm"
          danger
        >
          <ModalHeader
            title={t("alerts.events.clearConfirmTitle")}
            closeModal={() => {
              if (!alerts.isClearingEvents) {
                setClearHistoryOpen(false);
              }
            }}
          />
          <ModalBody>
            <p className="app-supporting-copy">{t("alerts.events.clearConfirmMessage")}</p>
          </ModalBody>
          <ModalFooter>
            <Button
              kind="secondary"
              disabled={alerts.isClearingEvents}
              onClick={() => setClearHistoryOpen(false)}
            >
              {t("alerts.events.clearConfirmCancel")}
            </Button>
            <Button
              kind="danger"
              data-testid="alerts-clear-history-confirm"
              disabled={alerts.isClearingEvents}
              onClick={() => {
                alerts.clearEvents({
                  onSuccess: () => {
                    setClearHistoryOpen(false);
                  },
                });
              }}
            >
              {t("alerts.events.clearConfirmAction")}
            </Button>
          </ModalFooter>
        </ComposedModal>
      ) : null}
    </Grid>
  );
}

function formatPrice(value: number, currency: string | undefined) {
  if (currency === "USD" || currency === "TWD") {
    return formatCurrency(value, currency);
  }
  return value.toFixed(2);
}

function ruleTypeLabel(
  ruleType: AlertRuleType,
): "alerts.ruleType.priceAbove" | "alerts.ruleType.priceBelow" {
  return ruleType === "price_above"
    ? "alerts.ruleType.priceAbove"
    : "alerts.ruleType.priceBelow";
}
