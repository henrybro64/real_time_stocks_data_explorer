/** Slowest poll used while a query is failing, so a dashboard heals after an outage (#285). */
export const ERROR_REFETCH_MS = 60_000;

/**
 * Keep polling a failed query, but no faster than ERROR_REFETCH_MS.
 * Returning `false` on error would freeze the page until a window refocus.
 */
export function refetchIntervalWhileErroring(
  status: string,
  intervalMs: number | false | undefined,
): number | false {
  if (intervalMs === false || intervalMs === undefined) {
    return false;
  }
  if (status !== "error") {
    return intervalMs;
  }
  return Math.max(intervalMs, ERROR_REFETCH_MS);
}
