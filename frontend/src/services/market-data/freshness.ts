import type { QuoteFreshnessMeta } from "@shared/quote";

export type QuoteFreshness =
  | "live"
  | "delayed"
  | "stale"
  | "closed"
  | "refreshing"
  | "unavailable";

type Options = {
  now?: Date;
  refreshFailed?: boolean;
  /** Prefer API cache-first metadata when present. */
  meta?: QuoteFreshnessMeta | null;
  /** True when the envelope has no quote payload yet. */
  hasQuote?: boolean;
};

export function getQuoteFreshness(
  timestamp: string,
  isMarketOpen: boolean,
  options: Options = {},
): QuoteFreshness {
  const meta = options.meta;
  if (meta != null) {
    return freshnessFromMeta(meta, {
      isMarketOpen,
      hasQuote: options.hasQuote ?? true,
      refreshFailed: options.refreshFailed ?? false,
    });
  }

  const asOf = new Date(timestamp);
  if (Number.isNaN(asOf.getTime())) {
    return "stale";
  }

  if (!isMarketOpen) {
    return "closed";
  }

  const now = options.now ?? new Date();
  const ageMinutes = Math.max(0, (now.getTime() - asOf.getTime()) / 60_000);

  if (options.refreshFailed && ageMinutes >= 2) {
    return "stale";
  }
  if (ageMinutes <= 2) {
    return "live";
  }
  if (ageMinutes <= 15) {
    return "delayed";
  }
  return "stale";
}

function freshnessFromMeta(
  meta: QuoteFreshnessMeta,
  options: { isMarketOpen: boolean; hasQuote: boolean; refreshFailed: boolean },
): QuoteFreshness {
  if (!options.hasQuote) {
    if (meta.refreshStatus === "unavailable") {
      return "unavailable";
    }
    return "refreshing";
  }
  if (options.refreshFailed || meta.stale) {
    if (meta.refreshStatus === "queued" || meta.refreshStatus === "refreshing") {
      return "refreshing";
    }
    return "stale";
  }
  if (!options.isMarketOpen) {
    return "closed";
  }
  if (meta.refreshStatus === "queued" || meta.refreshStatus === "refreshing") {
    return "refreshing";
  }
  const ageSeconds = meta.ageSeconds ?? 0;
  if (ageSeconds <= 120) {
    return "live";
  }
  if (ageSeconds <= 900) {
    return "delayed";
  }
  return "stale";
}
