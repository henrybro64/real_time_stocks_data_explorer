import type { QuoteResponse } from "@shared/api";

/**
 * Bound how long the dashboard treats null+queued/refreshing as "warming".
 * After this, UI flips to unavailable/retry even if an older backend still
 * reports queued. Driven by poll re-renders; no standalone timer.
 */
export const QUOTE_WARMING_TIMEOUT_MS = 30_000;

/**
 * True when a batch item is still warming (null quote + refresh in flight).
 */
export function isNullQuoteWarming(item: QuoteResponse): boolean {
  if (item.quote != null) {
    return false;
  }
  const status = item.freshness?.refreshStatus;
  return status === "queued" || status === "refreshing";
}

export function quotesAreWarming(quotes?: QuoteResponse[] | null): boolean {
  if (!quotes || quotes.length === 0) {
    return false;
  }
  return quotes.some(isNullQuoteWarming);
}

export function isQuoteWarmingTimedOut(
  warmingStartedAtMs: number | null | undefined,
  nowMs: number,
  timeoutMs: number = QUOTE_WARMING_TIMEOUT_MS,
): boolean {
  return warmingStartedAtMs != null && nowMs - warmingStartedAtMs >= timeoutMs;
}

/**
 * Single bookkeeping pass for dashboard warming clocks.
 * Updates `startedById` in place and returns per-id timeout flags.
 */
export function resolveWarmingTimeoutState(
  items: ReadonlyArray<QuoteResponse>,
  startedById: Map<string, number>,
  nowMs: number,
  timeoutMs: number = QUOTE_WARMING_TIMEOUT_MS,
): { timedOutById: Record<string, boolean>; allWarmingTimedOut: boolean } {
  const timedOutById: Record<string, boolean> = {};
  const activeIds = new Set<string>();
  let anyWarming = false;
  let allWarmingTimedOut = true;

  for (const item of items) {
    const stockId = item.stock.id;
    if (!isNullQuoteWarming(item)) {
      startedById.delete(stockId);
      timedOutById[stockId] = false;
      continue;
    }
    activeIds.add(stockId);
    anyWarming = true;
    let startedAt = startedById.get(stockId);
    if (startedAt == null) {
      startedAt = nowMs;
      startedById.set(stockId, startedAt);
    }
    const timedOut = isQuoteWarmingTimedOut(startedAt, nowMs, timeoutMs);
    timedOutById[stockId] = timedOut;
    if (!timedOut) {
      allWarmingTimedOut = false;
    }
  }

  for (const stockId of startedById.keys()) {
    if (!activeIds.has(stockId)) {
      startedById.delete(stockId);
    }
  }

  return {
    timedOutById,
    allWarmingTimedOut: anyWarming && allWarmingTimedOut,
  };
}
