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
  // Research-run nested progress shape
  processed?: number | null;
  total?: number | null;
};

export type FirstPassDrainLike = {
  armed?: boolean | null;
  available?: boolean | null;
  reason?: string | null;
};

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

export function drainAllowsAutomaticCollection(
  drain: FirstPassDrainLike | null | undefined,
  scheduleEnabled?: boolean | null,
): boolean {
  if (scheduleEnabled === true) return true;
  return Boolean(drain?.armed && drain?.available);
}

/**
 * Map durable job status into investor-facing buckets.
 * Never label "running" when nothing has been processed yet.
 * All-failed processed batches override to `all_failed` (not ready / collecting).
 */
export function firstPassPlainStatusKey(
  progress: FirstPassProgressLike | null | undefined,
): string {
  const status = String(progress?.status ?? "").trim().toLowerCase();
  const counts = normalizeFirstPassCounts(progress);
  const processed = counts?.processed ?? 0;
  const cursor = Number(progress?.cursor ?? 0);
  const hasSnapshot = Boolean(progress?.activeSnapshotId);

  if (!status) return "idle";

  // Honesty: HTTP-success batches with 0 usable rows must not look ready.
  if (batchLooksAllFailed(counts)) return "all_failed";

  if (status === "failed" || status === "cancelled") return "failed";
  if (status === "deferred") return "waiting_retry";
  if (status === "budget_exhausted") return "budget_exhausted";
  if (
    status === "pending" ||
    status === "queued" ||
    ((status === "running" || status === "partial") &&
      processed === 0 &&
      cursor === 0)
  ) {
    return "not_started";
  }
  if (status === "completed" || status === "partial") {
    if (hasSnapshot) return "ready_for_candidates";
    return "collecting";
  }
  if (status === "running") return "collecting";
  return "collecting";
}

export type NextActionOptions = {
  drain?: FirstPassDrainLike | null;
  scheduleEnabled?: boolean | null;
  browsingHistorical?: boolean;
};

/**
 * Investor next-action key. Must not promise automatic collection unless drain/schedule says so.
 */
export function firstPassNextActionKey(
  bucket: string,
  options: NextActionOptions = {},
): string {
  if (options.browsingHistorical) return "browsing_historical";
  if (bucket === "all_failed") return "all_failed";
  if (bucket === "failed") return "failed";
  if (bucket === "budget_exhausted") return "budget_exhausted";
  if (bucket === "idle") return "idle";
  if (bucket === "ready_for_candidates") return "ready_for_candidates";
  if (bucket === "waiting_retry") return "waiting_retry";
  if (bucket === "collecting") return "collecting";
  if (bucket === "not_started") {
    return drainAllowsAutomaticCollection(options.drain, options.scheduleEnabled)
      ? "not_started_auto"
      : "not_started_manual";
  }
  return "default";
}
