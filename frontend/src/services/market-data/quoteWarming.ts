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
