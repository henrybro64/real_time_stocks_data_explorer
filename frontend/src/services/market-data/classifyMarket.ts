import type { MarketCode } from "@shared/stock";

/** Watchlist/profile grouping. `unknown` is kept visible so a bad id is not dropped. */
export type ClassifiedMarket = MarketCode | "unknown";

export type MarketClassificationInput = {
  /** Authoritative domain market when the API already classified the stock. */
  market?: string | null;
  exchange?: string | null;
  symbol?: string | null;
  /** Canonical id such as `TW:2330` or `US:AAPL`. */
  id?: string | null;
};

const TAIWAN_EXCHANGES = new Set(["TWSE", "TSE", "TPEX", "TWO", "OTC"]);
const US_EXCHANGES = new Set(["NASDAQ", "NYSE", "NYSEARCA", "AMEX", "BATS", "NYSEMKT", "ARCA"]);

/**
 * Classify a tracked symbol without a second watchlist.
 *
 * Prefer `market` from the stock record, then the canonical id prefix.
 * `.TWO` is checked before `.TW` so an OTC ticker is not mistaken for a
 * TWSE suffix. A bare numeric code is not treated as a U.S. ticker.
 */
export function classifyMarket(input: MarketClassificationInput): ClassifiedMarket {
  const market = normalizeToken(input.market);
  if (market === "TW" || market === "US") {
    return market;
  }

  const idMarket = normalizeToken(input.id?.split(":")[0]);
  if ((idMarket === "TW" || idMarket === "US") && input.id?.includes(":")) {
    return idMarket;
  }

  const symbol = (input.symbol ?? idRemainder(input.id) ?? "").trim();
  const suffix = listingSuffix(symbol);
  if (suffix) {
    return "TW";
  }

  const exchange = normalizeToken(input.exchange);
  if (exchange && TAIWAN_EXCHANGES.has(exchange)) {
    return "TW";
  }
  if (exchange && US_EXCHANGES.has(exchange)) {
    return "US";
  }

  if (isUsTicker(symbol)) {
    return "US";
  }

  return "unknown";
}

/**
 * Profile display code: `2330.TW`, `3081.TWO`, or `NVDA`.
 * A Taiwan listing with no exchange keeps its bare code so TPEx is not labeled `.TW`.
 */
export function formatListingSymbol(input: MarketClassificationInput): string {
  const raw = (input.symbol?.trim() || idRemainder(input.id) || "").trim();
  if (!raw) {
    return input.id?.trim() ?? "";
  }

  const suffix = listingSuffix(raw);
  if (suffix) {
    return `${raw.slice(0, -suffix.length)}${suffix}`;
  }

  if (classifyMarket({ ...input, symbol: raw }) !== "TW") {
    return raw;
  }

  const exchange = normalizeToken(input.exchange);
  if (exchange === "TPEX" || exchange === "TWO" || exchange === "OTC") {
    return `${raw}.TWO`;
  }
  if (exchange === "TWSE" || exchange === "TSE") {
    return `${raw}.TW`;
  }
  return raw;
}

export function partitionByMarket<T>(
  items: readonly T[],
  classify: (item: T) => ClassifiedMarket,
): Record<ClassifiedMarket, T[]> {
  const groups: Record<ClassifiedMarket, T[]> = {
    TW: [],
    US: [],
    unknown: [],
  };
  for (const item of items) {
    groups[classify(item)].push(item);
  }
  return groups;
}

function listingSuffix(symbol: string): ".TWO" | ".TW" | null {
  const upper = symbol.toUpperCase();
  if (upper.endsWith(".TWO")) {
    return ".TWO";
  }
  if (upper.endsWith(".TW")) {
    return ".TW";
  }
  return null;
}

function isUsTicker(symbol: string): boolean {
  return /^[A-Za-z]{1,5}([.-][A-Za-z]{1,2})?$/.test(symbol);
}

function idRemainder(id: string | null | undefined): string | null {
  if (!id?.includes(":")) {
    return null;
  }
  const remainder = id.slice(id.indexOf(":") + 1).trim();
  return remainder || null;
}

function normalizeToken(value: string | null | undefined): string {
  return value?.trim().toUpperCase() ?? "";
}
