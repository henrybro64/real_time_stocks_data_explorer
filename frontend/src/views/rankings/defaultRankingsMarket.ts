import type { MarketStatus } from "@shared/api";
import type { MarketCode } from "@shared/stock";

/** Time-aware default for rankings: pre-open hour favors that market; if both apply, prefer TW. */
export function defaultRankingsMarketFromStatuses(statuses: MarketStatus[]): MarketCode {
  const tw = statuses.find((s) => s.market === "TW");
  const us = statuses.find((s) => s.market === "US");
  const twSoon = tw?.withinHourBeforeOpen === true;
  const usSoon = us?.withinHourBeforeOpen === true;
  if (twSoon && usSoon) return "TW";
  if (twSoon) return "TW";
  if (usSoon) return "US";
  return "TW";
}
