"use client";

import { Tile } from "@carbon/react";
import type { ReactNode } from "react";

import { useLanguage, type TranslationKey } from "../../app/i18n";
import {
  batchLooksAllFailed,
  firstPassNextActionKey,
  firstPassPlainStatusKey,
  normalizeFirstPassCounts,
  type FirstPassDrainLike,
  type FirstPassProgressLike,
} from "./researchStatusCopy";

type Props = {
  progress: FirstPassProgressLike | null | undefined;
  /** Job id whose progress is shown (selected / browsed). */
  displayedJobId?: string | null;
  /** Authoritative run-owned job id when different from displayed. */
  runOwnedJobId?: string | null;
  browsingHistorical?: boolean;
  drain?: FirstPassDrainLike | null;
  scheduleEnabled?: boolean | null;
  showBatchAllFailed?: boolean;
};

function plainStatusTranslationKey(bucket: string): TranslationKey {
  switch (bucket) {
    case "not_started":
      return "research.tw.plainStatus.not_started";
    case "collecting":
      return "research.tw.plainStatus.collecting";
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
    case "collecting":
      return "research.tw.nextAction.collecting";
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

export function ResearchInvestorSummary({
  progress,
  displayedJobId,
  runOwnedJobId,
  browsingHistorical = false,
  drain = null,
  scheduleEnabled = null,
  showBatchAllFailed = false,
}: Props) {
  const { t } = useLanguage();
  const counts = normalizeFirstPassCounts(progress);
  const bucket = firstPassPlainStatusKey(progress);
  const allFailed = showBatchAllFailed || batchLooksAllFailed(counts) || bucket === "all_failed";
  const nextAction = firstPassNextActionKey(bucket, {
    drain,
    scheduleEnabled,
    browsingHistorical,
  });
  const universe = counts?.universe ?? 0;
  const processed = counts?.processed ?? 0;
  const pct =
    universe > 0 ? Math.min(100, Math.round((processed / universe) * 100)) : 0;
  const nextRetryAt =
    typeof progress?.nextRetryAt === "string" && progress.nextRetryAt.trim()
      ? progress.nextRetryAt
      : null;
  const updatedAt =
    typeof progress?.updatedAt === "string" && progress.updatedAt.trim()
      ? progress.updatedAt
      : null;

  return (
    <Tile data-testid="tw-research-investor-summary">
      <h3 className="cds--productive-heading-02">{t("research.tw.investorSummaryTitle")}</h3>
      <p className="cds--helper-text-01">{t("research.tw.howToUse")}</p>
      {browsingHistorical ? (
        <p
          className="cds--helper-text-01"
          data-testid="tw-research-browsing-historical"
          role="status"
        >
          {t("research.tw.browsingHistoricalNote", {
            displayedJobId: displayedJobId ?? "—",
            runOwnedJobId: runOwnedJobId ?? "—",
          })}
        </p>
      ) : null}
      {displayedJobId ? (
        <p className="cds--helper-text-01" data-testid="tw-research-active-job">
          {t(
            browsingHistorical
              ? "research.tw.browsedJobLabel"
              : "research.tw.activeJobLabel",
            { jobId: displayedJobId },
          )}
        </p>
      ) : null}
      {!browsingHistorical && runOwnedJobId && runOwnedJobId !== displayedJobId ? (
        <p className="cds--helper-text-01" data-testid="tw-research-run-owned-job">
          {t("research.tw.activeJobLabel", { jobId: runOwnedJobId })}
        </p>
      ) : null}
      <p
        className="cds--body-compact-01"
        data-testid="tw-research-plain-status"
      >
        {t(plainStatusTranslationKey(bucket))}
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
            aria-label={t("research.tw.investorSummaryTitle")}
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
      {allFailed ? (
        <p
          className="cds--body-compact-01"
          data-testid="tw-research-batch-all-failed"
          role="status"
        >
          {t("research.tw.batchAllFailed", {
            failed: counts?.failed ?? 0,
            processed: counts?.processed ?? 0,
          })}
        </p>
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
