import { describe, expect, it } from "vitest";

import {
  marketFromStockId,
  PRICE_STROKE_COLORS,
  priceColorConventionFromMarket,
  priceColorConventionFromStockId,
  priceStrokeColor,
  priceToneClass,
  priceToneClassForDisplayCurrency,
  priceToneClassForMarket,
} from "./priceTone";

describe("priceTone", () => {
  it("detects market from canonical stock ids", () => {
    expect(marketFromStockId("TW:2330")).toBe("TW");
    expect(marketFromStockId("US:AAPL")).toBe("US");
    expect(marketFromStockId("EUR:XYZ")).toBeNull();
  });

  it("maps markets to price color conventions using exact MarketCode", () => {
    expect(priceColorConventionFromMarket("TW")).toBe("taiwan");
    expect(priceColorConventionFromMarket("US")).toBe("us");
    expect(priceColorConventionFromStockId("TW:6488")).toBe("taiwan");
    expect(priceColorConventionFromStockId("US:NVDA")).toBe("us");
    expect(priceColorConventionFromMarket(null)).toBe("us");
    // Exchange labels must not be treated as Taiwan market codes.
    expect(priceColorConventionFromMarket("TWSE")).toBe("us");
    expect(priceColorConventionFromMarket("TPEx")).toBe("us");
  });

  it("applies U.S. convention: up green class, down red class", () => {
    expect(priceToneClass("us", 1.5)).toBe("app-price-up app-price-convention-us");
    expect(priceToneClass("us", 0)).toBe("app-price-up app-price-convention-us");
    expect(priceToneClass("us", -0.5)).toBe("app-price-down app-price-convention-us");
    expect(priceStrokeColor("us", true)).toBe(PRICE_STROKE_COLORS.us.up);
    expect(priceStrokeColor("us", false)).toBe(PRICE_STROKE_COLORS.us.down);
  });

  it("applies Taiwan convention: up red class, down green class", () => {
    expect(priceToneClass("taiwan", 1.5)).toBe("app-price-up app-price-convention-taiwan");
    expect(priceToneClass("taiwan", -0.5)).toBe("app-price-down app-price-convention-taiwan");
    expect(priceStrokeColor("taiwan", true)).toBe(PRICE_STROKE_COLORS.taiwan.up);
    expect(priceStrokeColor("taiwan", false)).toBe(PRICE_STROKE_COLORS.taiwan.down);
  });

  it("derives text tone classes from market metadata", () => {
    expect(priceToneClassForMarket("TW", 12)).toContain("app-price-convention-taiwan");
    expect(priceToneClassForMarket("US", -3)).toContain("app-price-convention-us");
  });

  it("maps aggregate display-currency gains to the matching market convention", () => {
    expect(priceToneClassForDisplayCurrency("TWD", 12)).toBe(
      "app-price-up app-price-convention-taiwan",
    );
    expect(priceToneClassForDisplayCurrency("TWD", -12)).toBe(
      "app-price-down app-price-convention-taiwan",
    );
    expect(priceToneClassForDisplayCurrency("USD", 12)).toBe(
      "app-price-up app-price-convention-us",
    );
    expect(priceToneClassForDisplayCurrency("USD", -12)).toBe(
      "app-price-down app-price-convention-us",
    );
    expect(priceToneClassForDisplayCurrency("TWD", 0)).toBeUndefined();
    expect(priceToneClassForDisplayCurrency("USD", null)).toBeUndefined();
  });

  it("keeps price tone classes distinct from semantic status classes", () => {
    expect(priceToneClass("taiwan", 1)).not.toContain("app-positive");
    expect(priceToneClass("taiwan", -1)).not.toContain("app-negative");
    expect(priceToneClass("us", 1)).not.toContain("app-positive");
    expect(priceToneClass("us", -1)).not.toContain("app-negative");
  });
});
