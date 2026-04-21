export type QuoteFreshness = "live" | "delayed" | "stale" | "closed";

type Options = {
  now?: Date;
  refreshFailed?: boolean;
};

export function getQuoteFreshness(
  timestamp: string,
  isMarketOpen: boolean,
  options: Options = {},
): QuoteFreshness {
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
