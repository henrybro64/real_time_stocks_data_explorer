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
      timestamp,
      isMarketOpen,
      hasQuote: options.hasQuote ?? true,
      refreshFailed: options.refreshFailed ?? false,
      now: options.now ?? new Date(),
    });
  }

  return freshnessFromTimestamp(timestamp, isMarketOpen, {
    now: options.now ?? new Date(),
    refreshFailed: options.refreshFailed ?? false,
  });
}

function freshnessFromTimestamp(
  timestamp: string,
  isMarketOpen: boolean,
  options: { now: Date; refreshFailed: boolean },
): QuoteFreshness {
  const asOf = new Date(timestamp);
  if (Number.isNaN(asOf.getTime())) {
    return "stale";
  }

  if (!isMarketOpen) {
    return "closed";
  }

  const ageMinutes = Math.max(0, (options.now.getTime() - asOf.getTime()) / 60_000);

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
  options: {
    timestamp: string;
    isMarketOpen: boolean;
    hasQuote: boolean;
    refreshFailed: boolean;
    now: Date;
  },
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

  const quoteAgeSeconds = ageSecondsFromTimestamp(options.timestamp, options.now);
  const cacheAgeSeconds = meta.ageSeconds ?? 0;
  const ageSeconds = Math.max(cacheAgeSeconds, quoteAgeSeconds ?? 0);
  if (quoteAgeSeconds == null && meta.ageSeconds == null) {
    return "stale";
  }
  if (ageSeconds <= 120) {
    return "live";
  }
  if (ageSeconds <= 900) {
    return "delayed";
  }
  return "stale";
}

function ageSecondsFromTimestamp(timestamp: string, now: Date): number | null {
  const asOf = new Date(timestamp);
  if (Number.isNaN(asOf.getTime())) {
    return null;
  }
  return Math.max(0, (now.getTime() - asOf.getTime()) / 1000);
}
