import type { TimeRange } from "@shared/quote";

export const HISTORY_RANGES: TimeRange[] = ["1D", "1W", "1M", "3M", "6M", "1Y"];

export function stepHistoryRange(current: TimeRange, direction: "in" | "out"): TimeRange {
  const index = HISTORY_RANGES.indexOf(current);
  if (index < 0) {
    return "1M";
  }
  if (direction === "in") {
    return HISTORY_RANGES[Math.max(0, index - 1)];
  }
  return HISTORY_RANGES[Math.min(HISTORY_RANGES.length - 1, index + 1)];
}

export function getHistoryZoomState(current: TimeRange): {
  canZoomIn: boolean;
  canZoomOut: boolean;
} {
  const index = HISTORY_RANGES.indexOf(current);
  return {
    canZoomIn: index > 0,
    canZoomOut: index >= 0 && index < HISTORY_RANGES.length - 1,
  };
}
