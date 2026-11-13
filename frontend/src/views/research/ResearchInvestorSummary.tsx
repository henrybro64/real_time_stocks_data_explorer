"use client";

import { Tile } from "@carbon/react";
import type { ReactNode } from "react";

import { useLanguage, type TranslationKey } from "../../app/i18n";
import {
  firstPassBucketIsUnfinished,
  firstPassNextActionKey,
  firstPassPlainStatusKey,
  formatTaipeiDate,
  formatTaipeiDateTime,
  normalizeFirstPassCounts,
  type FailureReasonClass,
  type FirstPassDrainLike,
  type FirstPassProgressLike,
  type NowLike,
} from "./researchStatusCopy";

/** Result of the most recent manual batch only (never cumulative job counts). */
export type BatchResultView = {
  /** processed = symbols finished; lease_held / batch_incomplete = no-op HTTP 200. */
  kind?: "processed" | "lease_held" | "batch_incomplete";
  processed: number;
  ok: number;
  partial: number;
  failed: number;
  failures: Array<{
    stockId: string;
    label: string;
    reasonClass: FailureReasonClass;
  }>;
};

type Props = {
  progress: FirstPassProgressLike | null | undefined;
  /** Job id whose progress is shown (selected / browsed). */
  displayedJobId?: string | null;
  /** Authoritative run-owned job id when different from displayed. */
  runOwnedJobId?: string | null;
  browsingHistorical?: boolean;
  drain?: FirstPassDrainLike | null;
  scheduleEnabled?: boolean | null;
  /** ISO creation times used for date labels instead of internal job ids. */
  displayedJobCreatedAt?: string | null;
  runOwnedJobCreatedAt?: string | null;
  /** Latest manual batch result (from the batch mutation only). */
  batchResult?: BatchResultView | null;
  /** Progress/run refresh failed but cached data is shown. */
  stale?: boolean;
  staleAsOf?: string | null;
  /** Injectable clock for deterministic tests. */
  now?: NowLike;
};

function plainStatusTranslationKey(bucket: string): TranslationKey {
  switch (bucket) {
    case "not_started":
      return "research.tw.plainStatus.not_started";
    case "collecting_now":
      return "research.tw.plainStatus.collecting_now";
    case "paused_auto":
      return "research.tw.plainStatus.paused_auto";
    case "paused_auto_stale":
      return "research.tw.plainStatus.paused_auto_stale";
    case "paused_manual":
      return "research.tw.plainStatus.paused_manual";
    case "waiting_retry":
      return "research.tw.plainStatus.waiting_retry";
    case "failed":
      return "research.tw.plainStatus.failed";
    case "all_failed":
      return "research.tw.plainStatus.all_failed";
    case "budget_exhausted":
      return "research.tw.plainStatus.budget_exhausted";
    case "ready_for_candidates":
      return "research.tw.plainStatus.ready_for_candidates";
    default:
      return "research.tw.plainStatus.idle";
  }
}

function nextActionTranslationKey(action: string): TranslationKey {
  switch (action) {
    case "not_started_auto":
      return "research.tw.nextAction.not_started_auto";
    case "not_started_manual":
      return "research.tw.nextAction.not_started_manual";
    case "not_started":
      return "research.tw.nextAction.not_started_manual";
    case "collecting_now":
      return "research.tw.nextAction.collecting_now";
    case "paused_auto":
      return "research.tw.nextAction.paused_auto";
    case "paused_auto_stale":
      return "research.tw.nextAction.paused_auto_stale";
    case "paused_manual":
      return "research.tw.nextAction.paused_manual";
    case "waiting_retry":
      return "research.tw.nextAction.waiting_retry";
    case "failed":
      return "research.tw.nextAction.failed";
    case "all_failed":
      return "research.tw.nextAction.all_failed";
    case "budget_exhausted":
      return "research.tw.nextAction.budget_exhausted";
    case "ready_for_candidates":
      return "research.tw.nextAction.ready_for_candidates";
    case "browsing_historical":
      return "research.tw.nextAction.browsing_historical";
    case "idle":
      return "research.tw.nextAction.idle";
    default:
      return "research.tw.nextAction.default";
  }
}

function failureReasonKey(reason: FailureReasonClass): TranslationKey {
  switch (reason) {
    case "rate_limited":
      return "research.tw.batchFailureReason.rate_limited";
    case "programming_error":
      return "research.tw.batchFailureReason.programming_error";
    case "permanent":
      return "research.tw.batchFailureReason.permanent";
    case "retry_exhausted":
      return "research.tw.batchFailureReason.retry_exhausted";
    default:
      return "research.tw.batchFailureReason.transient";
  }
}

export function ResearchInvestorSummary({
  progress,
  displayedJobId,
  runOwnedJobId,
  displayedJobCreatedAt = null,
  runOwnedJobCreatedAt = null,
  browsingHistorical = false,
  drain = null,
  scheduleEnabled = null,
  batchResult = null,
  stale = false,
  staleAsOf = null,
  now,
}: Props) {
  const { t, language } = useLanguage();
  const locale = language === "en" ? "en-US" : "zh-TW";
  const counts = normalizeFirstPassCounts(progress);
  // Drain only applies to the run-owned job; historical browsing must not
  // promise automatic collection that drain_first_pass will refuse.
  const effectiveDrain = browsingHistorical ? null : drain;
  const effectiveSchedule = browsingHistorical ? null : scheduleEnabled;
  const bucket = firstPassPlainStatusKey(progress, {
    drain: effectiveDrain,
    scheduleEnabled: effectiveSchedule,
    now,
    stale,
  });
  const allFailed = bucket === "all_failed";
  const nextAction = firstPassNextActionKey(bucket, {
    drain: effectiveDrain,
    scheduleEnabled: effectiveSchedule,
    browsingHistorical,
  });
  const universe = counts?.universe ?? 0;
  const processed = counts?.processed ?? 0;
  const pct =
    universe > 0 ? Math.min(100, Math.round((processed / universe) * 100)) : 0;
  const nextRetryAt = formatTaipeiDateTime(progress?.nextRetryAt, locale);
  const updatedAt = formatTaipeiDateTime(progress?.updatedAt, locale);
  const lastTickAt = formatTaipeiDateTime(effectiveDrain?.lastTickAt, locale);
  const staleTime =
    formatTaipeiDateTime(staleAsOf, locale) ||
    formatTaipeiDateTime(progress?.updatedAt, locale) ||
    t("research.tw.dateUnknown");
  const dateOrUnknown = (iso: string | null | undefined) =>
    formatTaipeiDate(iso, locale) || t("research.tw.dateUnknown");
  const failuresSoFar =
    counts && counts.failed > 0 && firstPassBucketIsUnfinished(bucket)
      ? counts.failed
      : 0;
  const batchKind = batchResult?.kind ?? (batchResult ? "processed" : null);
  const batchAllFailed =
    batchResult !== null &&
    batchKind === "processed" &&
    batchResult.processed > 0 &&
    batchResult.failed === batchResult.processed;
  const plainStatusText = t(plainStatusTranslationKey(bucket), {
    time: lastTickAt,
  });

  return (
    <Tile data-testid="tw-research-investor-summary">
      <h3 className="cds--productive-heading-02">{t("research.tw.investorSummaryTitle")}</h3>
      <p className="cds--helper-text-01">{t("research.tw.howToUse")}</p>
      {stale ? (
        <p
          className="cds--body-compact-01"
          data-testid="tw-research-stale-banner"
          role="status"
        >
          {t("research.tw.staleLastKnown", { time: staleTime })}
        </p>
      ) : null}
      {browsingHistorical ? (
        <p
          className="cds--helper-text-01"
          data-testid="tw-research-browsing-historical"
          role="status"
        >
          {t("research.tw.browsingHistoricalNote", {
            displayedDate: dateOrUnknown(displayedJobCreatedAt),
            runOwnedDate: dateOrUnknown(runOwnedJobCreatedAt),
          })}
        </p>
      ) : null}
      {displayedJobId ? (
        <p className="cds--helper-text-01" data-testid="tw-research-active-job">
          {t(
            browsingHistorical
              ? "research.tw.browsedJobLabel"
              : "research.tw.activeJobLabel",
            { date: dateOrUnknown(displayedJobCreatedAt) },
          )}
        </p>
      ) : null}
      {!browsingHistorical && runOwnedJobId && runOwnedJobId !== displayedJobId ? (
        <p className="cds--helper-text-01" data-testid="tw-research-run-owned-job">
          {t("research.tw.activeJobLabel", {
            date: dateOrUnknown(runOwnedJobCreatedAt),
          })}
        </p>
      ) : null}
      <p
        className="cds--body-compact-01"
        data-testid="tw-research-plain-status"
        data-bucket={bucket}
      >
        {plainStatusText}
      </p>
      {counts ? (
        <>
          <p
            className="cds--body-compact-01"
            data-testid="tw-research-investor-counts"
          >
            {t("research.tw.investorSummaryCounts", {
              universe: counts.universe,
              processed: counts.processed,
              ok: counts.ok,
              partial: counts.partial,
              failed: counts.failed,
              pending: counts.pending,
            })}
          </p>
          <div
            data-testid="tw-research-progress-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label={t("research.tw.progressAria", { pct })}
            style={{
              height: 8,
              background: "var(--cds-border-subtle, #c6c6c6)",
              borderRadius: 4,
              overflow: "hidden",
              marginTop: 8,
              marginBottom: 8,
            }}
          >
            <div
              style={{
                width: `${pct}%`,
                height: "100%",
                background: allFailed
                  ? "var(--cds-support-error, #da1e28)"
                  : "var(--cds-support-info, #0043ce)",
              }}
            />
          </div>
          <p className="cds--helper-text-01">{t("research.tw.investorSummaryNote")}</p>
        </>
      ) : (
        <p className="cds--helper-text-01">{t("research.tw.plainStatus.idle")}</p>
      )}
      {failuresSoFar > 0 ? (
        <p
          className="cds--helper-text-01"
          data-testid="tw-research-failures-so-far"
        >
          {nextRetryAt
            ? t("research.tw.failuresScheduledRetry", {
                failed: failuresSoFar,
                time: nextRetryAt,
              })
            : t("research.tw.failuresSoFar", { failed: failuresSoFar })}
        </p>
      ) : null}
      {nextRetryAt ? (
        <p className="cds--helper-text-01" data-testid="tw-research-next-retry">
          {t("research.tw.nextRetryAt", { time: nextRetryAt })}
        </p>
      ) : null}
      {updatedAt ? (
        <p className="cds--helper-text-01" data-testid="tw-research-progress-updated">
          {t("research.tw.updatedAt", { time: updatedAt })}
        </p>
      ) : null}
      <p
        className="cds--body-compact-01"
        data-testid="tw-research-next-action"
      >
        {t(nextActionTranslationKey(nextAction))}
      </p>
      {batchResult ? (
        <div data-testid="tw-research-batch-result">
          {batchKind === "lease_held" ? (
            <p
              className="cds--body-compact-01"
              data-testid="tw-research-batch-lease-held"
              role="status"
            >
              {t("research.tw.batchLeaseHeld")}
            </p>
          ) : null}
          {batchKind === "batch_incomplete" ? (
            <p
              className="cds--body-compact-01"
              data-testid="tw-research-batch-incomplete"
              role="status"
            >
              {t("research.tw.batchIncomplete")}
            </p>
          ) : null}
          {batchKind === "processed" ? (
            <>
              <p className="cds--helper-text-01" data-testid="tw-research-batch-summary">
                {t("research.tw.batchResultSummary", {
                  processed: batchResult.processed,
                  ok: batchResult.ok,
                  partial: batchResult.partial,
                  failed: batchResult.failed,
                })}
              </p>
              {batchAllFailed ? (
                <p
                  className="cds--body-compact-01"
                  data-testid="tw-research-batch-all-failed"
                  role="status"
                >
                  {t("research.tw.batchAllFailed", { failed: batchResult.failed })}
                </p>
              ) : null}
              {batchResult.failures.length > 0 ? (
                <div data-testid="tw-research-batch-failed-symbols">
                  <p className="cds--helper-text-01">
                    {t("research.tw.batchFailedSymbolsLabel")}
                  </p>
                  <ul>
                    {batchResult.failures.map((failure) => (
                      <li
                        key={failure.stockId}
                        className="cds--helper-text-01"
                        data-testid={`tw-research-batch-failure-${failure.stockId}`}
                      >
                        {failure.label} — {t(failureReasonKey(failure.reasonClass))}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
      <details data-testid="tw-research-glossary" style={{ marginTop: 8 }}>
        <summary className="cds--helper-text-01">
          {t("research.tw.glossaryTitle")}
        </summary>
        <p className="cds--helper-text-01">{t("research.tw.glossaryFundamentals")}</p>
        <p className="cds--helper-text-01">{t("research.tw.glossaryMacdKd")}</p>
        <p className="cds--helper-text-01">{t("research.tw.glossaryChip")}</p>
        <p className="cds--helper-text-01">{t("research.tw.glossaryWatchlist")}</p>
      </details>
    </Tile>
  );
}

export function AdvancedDetailsDisclosure({
  children,
  defaultOpen = false,
}: {
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const { t } = useLanguage();
  return (
    <details
      data-testid="tw-research-advanced-details"
      open={defaultOpen || undefined}
      style={{ marginTop: 8 }}
    >
      <summary className="cds--productive-heading-01">
        {t("research.tw.advancedTitle")}
      </summary>
      <p className="cds--helper-text-01">{t("research.tw.advancedHint")}</p>
      {children}
    </details>
  );
}
