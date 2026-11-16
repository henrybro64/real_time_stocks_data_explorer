"use client";

import {
  InlineNotification,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
  Tile,
} from "@carbon/react";

import { useLanguage, type TranslationKey } from "../../app/i18n";
import { ApiError, toApiError } from "../../services/api/client";
import type {
  TwResearchTechnicalIndicatorSnapshot,
  TwResearchTechnicalResultCandidate,
  TwResearchTechnicalResults,
} from "../../services/api/twResearch";

import styles from "./TechnicalResultsPanel.module.scss";

function stockLabel(stockId: string, name?: string | null): string {
  const id = String(stockId ?? "");
  const nm = String(name ?? "").trim();
  return nm ? `${nm} (${id})` : id;
}

export function formatIndicatorValue(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(2);
}

export function insufficientHistoryReasonText(
  row: TwResearchTechnicalResultCandidate,
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string,
): string | null {
  const info = row.insufficientHistory;
  if (!info) return null;
  if (info.availableBars == null) {
    return t("research.tw.technicalResults.insufficientHistoryUnknown", {
      required: info.requiredBars,
    });
  }
  return t("research.tw.technicalResults.insufficientHistory", {
    required: info.requiredBars,
    available: info.availableBars,
  });
}

export function dailyNotRunReasonText(
  row: TwResearchTechnicalResultCandidate,
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string,
): string | null {
  if (!row.dailyNotRunReason && row.overallStageResult !== "daily_not_run") {
    return null;
  }
  return t("research.tw.technicalResults.dailyNotRun");
}

export function isTechnicalResultsNotFound(error: unknown): boolean {
  const apiError = error instanceof ApiError ? error : toApiError(error);
  return apiError.status === 404;
}

function passChipKind(
  result: string,
): "green" | "red" | "gray" | "blue" | "purple" | "teal" {
  if (result === "pass") return "green";
  if (
    result === "fail" ||
    result === "monthly_fail" ||
    result === "daily_fail" ||
    result === "error"
  ) {
    return "red";
  }
  if (result === "insufficient_history") return "purple";
  if (result === "waiting_for_history") return "teal";
  if (result === "daily_not_run") return "blue";
  return "gray";
}

const RESULT_LABEL_KEYS: Record<string, TranslationKey> = {
  pass: "research.tw.technicalResults.result.pass",
  fail: "research.tw.technicalResults.result.fail",
  error: "research.tw.technicalResults.result.error",
  insufficient_history: "research.tw.technicalResults.result.insufficient_history",
  waiting_for_history: "research.tw.technicalResults.result.waiting_for_history",
  daily_not_run: "research.tw.technicalResults.result.daily_not_run",
  monthly_pass: "research.tw.technicalResults.result.monthly_pass",
  monthly_fail: "research.tw.technicalResults.result.monthly_fail",
  pending: "research.tw.technicalResults.result.pending",
  deferred: "research.tw.technicalResults.result.deferred",
  monthly_unknown: "research.tw.technicalResults.result.monthly_unknown",
  daily_unknown: "research.tw.technicalResults.result.daily_unknown",
};

function passChipLabel(
  result: string,
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string,
): string {
  const key = RESULT_LABEL_KEYS[result];
  return key ? t(key) : result;
}

function IndicatorBlock({
  label,
  snapshot,
  asOfLabel,
  emptyReason,
}: {
  label: string;
  snapshot: TwResearchTechnicalIndicatorSnapshot | null | undefined;
  asOfLabel: string;
  emptyReason: string | null;
}) {
  if (!snapshot) {
    return (
      <div className={styles.indicatorBlock} data-testid="tw-research-tech-indicator-empty">
        <div className={styles.indicatorLabel}>{label}</div>
        <div className={styles.nullValue}>—</div>
        {emptyReason ? (
          <div className={styles.reason} data-testid="tw-research-tech-indicator-reason">
            {emptyReason}
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <div className={styles.indicatorBlock} data-testid="tw-research-tech-indicator">
      <div className={styles.indicatorLabel}>{label}</div>
      <div className={styles.asOf}>
        {asOfLabel}: {snapshot.asOf ?? "—"}
      </div>
      <div className={styles.metrics}>
        <span>DIF {formatIndicatorValue(snapshot.dif)}</span>
        <span>Signal {formatIndicatorValue(snapshot.signal)}</span>
        <span>OSC {formatIndicatorValue(snapshot.osc)}</span>
        <span>K {formatIndicatorValue(snapshot.k)}</span>
        <span>D {formatIndicatorValue(snapshot.d)}</span>
      </div>
      <div className={styles.bars}>
        bars {snapshot.barCount != null ? String(snapshot.barCount) : "—"}
      </div>
    </div>
  );
}

function ResultCard({
  row,
  t,
}: {
  row: TwResearchTechnicalResultCandidate;
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string;
}) {
  const insufficient = insufficientHistoryReasonText(row, t);
  const dailyNotRun = dailyNotRunReasonText(row, t);
  return (
    <article
      className={styles.card}
      data-testid="tw-research-technical-result-card"
      data-stock-id={row.stockId}
    >
      <header className={styles.cardHeader}>
        <strong>{stockLabel(row.stockId, row.name)}</strong>
        <Tag size="sm" type={passChipKind(row.overallStageResult)}>
          {passChipLabel(row.overallStageResult, t)}
        </Tag>
      </header>
      <IndicatorBlock
        label={t("research.tw.technicalResults.monthly")}
        snapshot={row.monthly}
        asOfLabel={t("research.tw.technicalResults.asOfMonth")}
        emptyReason={insufficient}
      />
      <IndicatorBlock
        label={t("research.tw.technicalResults.daily")}
        snapshot={row.daily}
        asOfLabel={t("research.tw.technicalResults.asOfDate")}
        emptyReason={dailyNotRun ?? insufficient}
      />
      {insufficient ? (
        <p className={styles.reason} data-testid="tw-research-tech-insufficient-reason">
          {insufficient}
        </p>
      ) : null}
      {dailyNotRun ? (
        <p className={styles.reason} data-testid="tw-research-tech-daily-not-run-reason">
          {dailyNotRun}
        </p>
      ) : null}
      {row.overallStageResult === "waiting_for_history" ? (
        <p className={styles.reason} data-testid="tw-research-tech-waiting-reason">
          {passChipLabel("waiting_for_history", t)}
        </p>
      ) : null}
    </article>
  );
}

export type TechnicalResultsPanelProps = {
  results: TwResearchTechnicalResults | null | undefined;
  phase2Message?: string | null;
  empty?: boolean;
  error?: unknown;
};

export function TechnicalResultsPanel({
  results,
  phase2Message,
  empty = false,
  error = null,
}: TechnicalResultsPanelProps) {
  const { t } = useLanguage();
  const candidates = results?.candidates ?? [];
  const showError = Boolean(error) && !isTechnicalResultsNotFound(error);
  const showEmpty =
    !showError && (empty || (!results && !error) || candidates.length === 0);

  return (
    <Tile data-testid="tw-research-technical-results">
      <Stack gap={3}>
        <div>
          <h2 className="cds--productive-heading-03">
            {t("research.tw.technicalResults.title")}
          </h2>
          <p className="cds--helper-text-01">
            {t("research.tw.technicalResults.subtitle")}
          </p>
        </div>
        {phase2Message ? (
          <p
            className="cds--helper-text-01"
            data-testid="tw-research-technical-results-phase2"
          >
            {phase2Message}
          </p>
        ) : null}
        {showError ? (
          <div data-testid="tw-research-technical-results-error">
            <InlineNotification
              kind="error"
              lowContrast
              hideCloseButton
              title=""
              subtitle={t("research.tw.technicalResults.loadError")}
            />
          </div>
        ) : null}
        {showEmpty ? (
          <p
            className="cds--body-compact-01"
            data-testid="tw-research-technical-results-empty"
          >
            {t("research.tw.technicalResults.empty")}
          </p>
        ) : null}
        {!showError && !showEmpty ? (
          <>
            <div className={styles.desktopTable} data-testid="tw-research-technical-results-table">
              <TableContainer>
                <Table size="sm">
                  <TableHead>
                    <TableRow>
                      <TableHeader>{t("research.tw.colStock")}</TableHeader>
                      <TableHeader>{t("research.tw.technicalResults.monthly")}</TableHeader>
                      <TableHeader>{t("research.tw.technicalResults.daily")}</TableHeader>
                      <TableHeader>{t("research.tw.technicalResults.result")}</TableHeader>
                      <TableHeader>{t("research.tw.technicalResults.bars")}</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {candidates.map((row) => {
                      const insufficient = insufficientHistoryReasonText(row, t);
                      const dailyNotRun = dailyNotRunReasonText(row, t);
                      return (
                        <TableRow
                          key={row.stockId}
                          data-testid="tw-research-technical-result-row"
                          data-stock-id={row.stockId}
                        >
                          <TableCell>{stockLabel(row.stockId, row.name)}</TableCell>
                          <TableCell>
                            {row.monthly ? (
                              <div>
                                <div>
                                  {t("research.tw.technicalResults.asOfMonth")}:{" "}
                                  {row.monthly.asOf ?? "—"}
                                </div>
                                <div>
                                  DIF {formatIndicatorValue(row.monthly.dif)} / Signal{" "}
                                  {formatIndicatorValue(row.monthly.signal)} / OSC{" "}
                                  {formatIndicatorValue(row.monthly.osc)}
                                </div>
                                <div>
                                  K {formatIndicatorValue(row.monthly.k)} / D{" "}
                                  {formatIndicatorValue(row.monthly.d)}
                                </div>
                              </div>
                            ) : (
                              <div>
                                <div>—</div>
                                {insufficient ? (
                                  <div
                                    className={styles.reason}
                                    data-testid="tw-research-tech-insufficient-reason"
                                  >
                                    {insufficient}
                                  </div>
                                ) : null}
                              </div>
                            )}
                          </TableCell>
                          <TableCell>
                            {row.daily ? (
                              <div>
                                <div>
                                  {t("research.tw.technicalResults.asOfDate")}:{" "}
                                  {row.daily.asOf ?? "—"}
                                </div>
                                <div>
                                  DIF {formatIndicatorValue(row.daily.dif)} / Signal{" "}
                                  {formatIndicatorValue(row.daily.signal)} / OSC{" "}
                                  {formatIndicatorValue(row.daily.osc)}
                                </div>
                                <div>
                                  K {formatIndicatorValue(row.daily.k)} / D{" "}
                                  {formatIndicatorValue(row.daily.d)}
                                </div>
                              </div>
                            ) : (
                              <div>
                                <div>—</div>
                                {dailyNotRun ? (
                                  <div
                                    className={styles.reason}
                                    data-testid="tw-research-tech-daily-not-run-reason"
                                  >
                                    {dailyNotRun}
                                  </div>
                                ) : null}
                              </div>
                            )}
                          </TableCell>
                          <TableCell>
                            <Tag size="sm" type={passChipKind(row.overallStageResult)}>
                              {passChipLabel(row.overallStageResult, t)}
                            </Tag>
                          </TableCell>
                          <TableCell>
                            {row.monthly?.barCount ?? row.daily?.barCount ?? "—"}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            </div>
            <div className={styles.mobileCards} data-testid="tw-research-technical-results-cards">
              {candidates.map((row) => (
                <ResultCard key={row.stockId} row={row} t={t} />
              ))}
            </div>
          </>
        ) : null}
      </Stack>
    </Tile>
  );
}
