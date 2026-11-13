/** Plain-language status mapping for TW research UI (#350/#351). */

export type FirstPassProgressLike = {
  status?: string | null;
  qualityStatus?: string | null;
  universeSize?: number | null;
  processedCount?: number | null;
  pendingCount?: number | null;
  okCount?: number | null;
  partialCount?: number | null;
  failedCount?: number | null;
  deferredCount?: number | null;
  cursor?: number | null;
  activeSnapshotId?: string | null;
  nextRetryAt?: string | null;
  updatedAt?: string | null;
  /** Additive (#350/#351): a batch lease is currently held by a live worker. */
  leaseActive?: boolean | null;
  leaseExpiresAt?: string | null;
  lastError?: string | null;
  // Research-run nested progress shape
  processed?: number | null;
  total?: number | null;
};

export type FirstPassDrainLike = {
  armed?: boolean | null;
  available?: boolean | null;
  reason?: string | null;
  lastTickAt?: string | null;
  lastOutcome?: string | null;
};

/** A drain tick older than this no longer counts as "recently ran". */
export const DRAIN_TICK_FRESH_MS = 15 * 60 * 1000;

export type InvestorSummaryCounts = {
  universe: number;
  processed: number;
  ok: number;
  partial: number;
  failed: number;
  deferred: number;
  pending: number;
};

export function normalizeFirstPassCounts(
  progress: FirstPassProgressLike | null | undefined,
): InvestorSummaryCounts | null {
  if (!progress) return null;
  const universe = Number(progress.universeSize ?? progress.total ?? 0);
  const processed = Number(progress.processedCount ?? progress.processed ?? 0);
  const ok = Number(progress.okCount ?? 0);
  const partial = Number(progress.partialCount ?? 0);
  const failed = Number(progress.failedCount ?? 0);
  const deferred = Number(progress.deferredCount ?? 0);
  const pending = Number(
    progress.pendingCount ?? Math.max(0, universe - processed),
  );
  if (!Number.isFinite(universe) || universe <= 0) {
    // Still allow zero-universe empty state if status is known.
    if (!progress.status) return null;
  }
  return {
    universe: Math.max(0, universe),
    processed: Math.max(0, processed),
    ok: Math.max(0, ok),
    partial: Math.max(0, partial),
    failed: Math.max(0, failed),
    deferred: Math.max(0, deferred),
    pending: Math.max(0, pending),
  };
}

export function batchLooksAllFailed(counts: InvestorSummaryCounts | null): boolean {
  if (!counts) return false;
  return (
    counts.processed > 0 &&
    counts.failed > 0 &&
    counts.ok === 0 &&
    counts.partial === 0 &&
    counts.failed === counts.processed
  );
}

/**
 * Automatic collection is promised only by `firstPassDrain` (armed + available).
 * `scheduleEnabled` is a legacy fallback used solely when the drain block is absent.
 */
export function drainAllowsAutomaticCollection(
  drain: FirstPassDrainLike | null | undefined,
  scheduleEnabled?: boolean | null,
): boolean {
  if (drain) return Boolean(drain.armed && drain.available);
  return scheduleEnabled === true;
}

export type NowLike = number | Date | (() => number | Date);

function nowMs(now: NowLike | undefined): number {
  const value = typeof now === "function" ? now() : now;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value;
  return Date.now();
}

/** True when the drain reported a tick within {@link DRAIN_TICK_FRESH_MS}. */
export function drainTickIsFresh(
  drain: FirstPassDrainLike | null | undefined,
  now?: NowLike,
): boolean {
  const raw = drain?.lastTickAt;
  if (typeof raw !== "string" || !raw.trim()) return false;
  const tick = Date.parse(raw);
  if (!Number.isFinite(tick)) return false;
  return nowMs(now) - tick <= DRAIN_TICK_FRESH_MS;
}

export type PlainStatusOptions = {
  drain?: FirstPassDrainLike | null;
  /** Legacy fallback; consulted only when `drain` is absent. */
  scheduleEnabled?: boolean | null;
  now?: NowLike;
  /** Last known state could not be refreshed: never claim collection is live. */
  stale?: boolean;
};

function isTerminalProgress(
  status: string,
  counts: InvestorSummaryCounts | null,
  hasSnapshot: boolean,
): boolean {
  if (status === "failed" || status === "cancelled" || status === "completed") {
    return true;
  }
  if (status === "partial") {
    // Job-list rows often omit counts; a durable snapshot means the attempt finished.
    if (hasSnapshot) return true;
    // `partial` is only terminal once every symbol has been attempted.
    return Boolean(counts && counts.universe > 0 && counts.pending === 0);
  }
  return false;
}

/** Every attempted symbol failed and nothing verified/partial exists. */
export function jobHasOnlyFailures(counts: InvestorSummaryCounts | null): boolean {
  if (!counts) return false;
  return (
    counts.processed > 0 &&
    counts.failed > 0 &&
    counts.ok === 0 &&
    counts.partial === 0
  );
}

/**
 * Map durable job status into investor-facing buckets.
 *
 * - `collecting_now` only when a live batch lease is held (and the data is fresh).
 * - Unfinished work with no lease is `paused_*`, never "collecting".
 * - Cumulative `all_failed` is reserved for terminal jobs with ok+partial == 0.
 */
export function firstPassPlainStatusKey(
  progress: FirstPassProgressLike | null | undefined,
  options: PlainStatusOptions = {},
): string {
  const status = String(progress?.status ?? "").trim().toLowerCase();
  const counts = normalizeFirstPassCounts(progress);
  const processed = counts?.processed ?? 0;
  const cursor = Number(progress?.cursor ?? 0);
  const hasSnapshot = Boolean(progress?.activeSnapshotId);

  if (!status) return "idle";

  if (status === "failed" || status === "cancelled") {
    return jobHasOnlyFailures(counts) ? "all_failed" : "failed";
  }
  if (status === "budget_exhausted") return "budget_exhausted";

  const terminal = isTerminalProgress(status, counts, hasSnapshot);
  if (terminal) {
    // Honesty: HTTP-success jobs with 0 usable rows must not look ready.
    if (jobHasOnlyFailures(counts)) return "all_failed";
    if (hasSnapshot) return "ready_for_candidates";
    // Completed but no snapshot yet: fall through to lease/drain based copy.
  }

  const leaseActive = Boolean(progress?.leaseActive) && !options.stale;
  if (leaseActive) return "collecting_now";

  if (status === "deferred") return "waiting_retry";
  if (
    status === "pending" ||
    status === "queued" ||
    ((status === "running" || status === "partial") &&
      processed === 0 &&
      cursor === 0)
  ) {
    return "not_started";
  }

  if (drainAllowsAutomaticCollection(options.drain, options.scheduleEnabled)) {
    return drainTickIsFresh(options.drain, options.now)
      ? "paused_auto"
      : "paused_auto_stale";
  }
  return "paused_manual";
}

/** True while the job still has symbols to collect (retry-scheduled included). */
export function firstPassBucketIsUnfinished(bucket: string): boolean {
  return (
    bucket === "collecting_now" ||
    bucket === "paused_auto" ||
    bucket === "paused_auto_stale" ||
    bucket === "paused_manual" ||
    bucket === "waiting_retry" ||
    bucket === "not_started"
  );
}

export type NextActionOptions = {
  drain?: FirstPassDrainLike | null;
  scheduleEnabled?: boolean | null;
  browsingHistorical?: boolean;
};

/**
 * Investor next-action key. Must not promise automatic collection unless drain says so.
 */
export function firstPassNextActionKey(
  bucket: string,
  options: NextActionOptions = {},
): string {
  if (options.browsingHistorical) return "browsing_historical";
  switch (bucket) {
    case "all_failed":
    case "failed":
    case "budget_exhausted":
    case "idle":
    case "ready_for_candidates":
    case "waiting_retry":
    case "collecting_now":
    case "paused_auto":
    case "paused_auto_stale":
    case "paused_manual":
      return bucket;
    case "not_started":
      return drainAllowsAutomaticCollection(options.drain, options.scheduleEnabled)
        ? "not_started_auto"
        : "not_started_manual";
    default:
      return "default";
  }
}

const TAIPEI_TZ = "Asia/Taipei";

function toDate(iso: unknown): Date | null {
  if (typeof iso !== "string" || !iso.trim()) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Normalize ICU spacing so formatted times stay portable across Node/ICU builds.
 * CI Linux often inserts U+2009 thin space (or U+202F) between date and time.
 */
function normalizeIntlSpaces(value: string): string {
  return value
    .replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g, " ")
    .replace(/ {2,}/g, " ")
    .trim();
}

/** Locale-aware Asia/Taipei date-time; empty string when `iso` is missing/invalid. */
export function formatTaipeiDateTime(iso: unknown, locale: string): string {
  const date = toDate(iso);
  if (!date) return "";
  return normalizeIntlSpaces(
    new Intl.DateTimeFormat(locale, {
      timeZone: TAIPEI_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date),
  );
}

/** Locale-aware Asia/Taipei calendar date; empty string when missing/invalid. */
export function formatTaipeiDate(iso: unknown, locale: string): string {
  const date = toDate(iso);
  if (!date) return "";
  return normalizeIntlSpaces(
    new Intl.DateTimeFormat(locale, {
      timeZone: TAIPEI_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(date),
  );
}

/** Failure classes → i18n key suffix for investor-facing per-symbol reasons. */
export type FailureReasonClass =
  | "transient"
  | "rate_limited"
  | "programming_error"
  | "permanent"
  | "retry_exhausted";

export function normalizeFailureClass(
  raw: unknown,
  meta?: { retryExhausted?: unknown },
): FailureReasonClass {
  if (meta?.retryExhausted === true) return "retry_exhausted";
  const value = String(raw ?? "").trim().toLowerCase();
  if (value === "rate_limited") return "rate_limited";
  if (value === "programming_error") return "programming_error";
  if (value === "permanent") return "permanent";
  if (value === "retry_exhausted") return "retry_exhausted";
  // timeout / transient / provider_validation / stale / unknown → retryable.
  return "transient";
}

/**
 * Map job-list qualityStatus to investor quality labels.
 * Backend sets `complete_attempted` on every finished first-pass job
 * (including failed/partial rows), so it must not read as "Complete".
 */
export function jobQualityPlainKey(
  qualityStatus: string | null | undefined,
): "complete" | "attempted" | "partial" | "incomplete" {
  const value = String(qualityStatus ?? "").trim().toLowerCase();
  if (value === "verified_ready") {
    return "complete";
  }
  if (value === "complete_attempted") {
    return "attempted";
  }
  if (
    value === "incomplete" ||
    value === "source_incomplete" ||
    value === "" ||
    !value
  ) {
    return "incomplete";
  }
  // partial / processed / and other partial-ish labels
  return "partial";
}

/** Plain-language keys for technical-scan status (#351 progress tile). */
export type TechnicalScanPlainStatusKey =
  | "not_started"
  | "collecting_now"
  | "paused"
  | "waiting_retry"
  | "budget_exhausted"
  | "processed_not_verified"
  | "verified"
  | "failed";

/**
 * Map technical-scan API status to investor status keys.
 * `running` is collecting only while a worker/lease is active; otherwise paused.
 */
export function technicalScanPlainStatusKey(
  status: string | null | undefined,
  options?: { workerActive?: boolean | null },
): TechnicalScanPlainStatusKey {
  const value = String(status ?? "").trim().toLowerCase();
  if (value === "queued" || value === "pending") return "not_started";
  if (value === "running") {
    return options?.workerActive ? "collecting_now" : "paused";
  }
  if (value === "partial" || value === "deferred") return "waiting_retry";
  if (value === "budget_exhausted") return "budget_exhausted";
  if (value === "completed_processing") return "processed_not_verified";
  if (value === "verified_ready") return "verified";
  if (value === "failed") return "failed";
  return "paused";
}

/** Plain-language keys for technical-scan quality (#351 progress tile). */
export type TechnicalScanQualityPlainKey =
  | "verified"
  | "attempted"
  | "partial"
  | "incomplete"
  | "unverified";

export function technicalScanQualityPlainKey(
  qualityStatus: string | null | undefined,
): TechnicalScanQualityPlainKey {
  const value = String(qualityStatus ?? "").trim().toLowerCase();
  if (value === "verified_ready") return "verified";
  if (value === "complete_attempted") return "attempted";
  if (value === "processed" || value === "partial") return "partial";
  if (value === "source_incomplete" || value === "incomplete") return "incomplete";
  return "unverified";
}

export type JobHistoryLabelJob = FirstPassProgressLike & {
  jobId?: string | null;
  createdAt?: string | null;
  qualityStatus?: string | null;
};

/**
 * Plain-language job history Select option: date · status · quality.
 * Raw snake_case status/quality stay out of the visible dropdown.
 */
export function jobHistoryOptionLabel(
  job: JobHistoryLabelJob,
  // Callers pass the app `t`; keys are built from known plain-status / quality maps.
  t: (key: never, params?: Record<string, string | number>) => string,
  language: "en" | "zh-TW",
): string {
  const locale = language === "en" ? "en-US" : "zh-TW";
  const translate = t as (key: string, params?: Record<string, string | number>) => string;
  const date =
    formatTaipeiDateTime(job.createdAt, locale) ||
    translate("research.tw.dateUnknown");
  // List jobs often omit lease; unfinished running without lease → paused_manual.
  const bucket = firstPassPlainStatusKey(job);
  const statusText = translate(`research.tw.plainStatus.${bucket}`, { time: "" });
  const qualityKey = jobQualityPlainKey(job.qualityStatus);
  const qualityText = translate(`research.tw.jobQuality.${qualityKey}`);
  return `${date} · ${statusText} · ${qualityText}`;
}
