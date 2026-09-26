import type { MarketCode } from "@shared/stock";

/** Visual convention for price direction colors. */
export type PriceColorConvention = "taiwan" | "us";

/**
 * Canonical price-direction hex colors.
 * Keep in sync with CSS vars under `[data-price-convention]` in global.css
 * (`--app-price-up` / `--app-price-down`).
 */
export const PRICE_STROKE_COLORS = {
  us: {
    up: "#24a148",
    down: "#da1e28",
  },
  taiwan: {
    up: "#da1e28",
    down: "#24a148",
  },
} as const;

export function priceColorConventionFromMarket(
  market: MarketCode | string | null | undefined,
): PriceColorConvention {
  if (market === "TW") {
    return "taiwan";
  }
  return "us";
}

/**
 * Text class for signed price movement. Uses >= 0 as "up" to match existing quote UI.
 * Keeps `.app-positive` / `.app-negative` free for non-price semantic status.
 */
export function priceToneClass(
  convention: PriceColorConvention,
  value: number,
): string {
  const direction = value >= 0 ? "up" : "down";
  return `app-price-${direction} app-price-convention-${convention}`;
}

export function priceToneClassForMarket(
  market: MarketCode | string | null | undefined,
  value: number,
): string {
  return priceToneClass(priceColorConventionFromMarket(market), value);
}

/** Aggregate P/L tone: TWD books use Taiwan convention; USD books use U.S. */
export function priceToneClassForDisplayCurrency(
  currency: "USD" | "TWD",
  value: number | null,
): string | undefined {
  if (value == null || value === 0) {
    return undefined;
  }
  return priceToneClass(currency === "TWD" ? "taiwan" : "us", value);
}

export function priceStrokeColor(convention: PriceColorConvention, isUp: boolean): string {
  return isUp ? PRICE_STROKE_COLORS[convention].up : PRICE_STROKE_COLORS[convention].down;
}
