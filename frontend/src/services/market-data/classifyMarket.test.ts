import { describe, expect, it } from "vitest";

import { classifyMarket, formatListingSymbol, partitionByMarket } from "./classifyMarket";

describe("classifyMarket", () => {
  it("prefers the stock record market over ticker shape", () => {
    expect(classifyMarket({ market: "TW", symbol: "NVDA", exchange: "NASDAQ" })).toBe("TW");
    expect(classifyMarket({ market: "US", symbol: "2330", exchange: "TWSE" })).toBe("US");
  });

  it("classifies canonical ids and Yahoo-style Taiwan suffixes", () => {
    expect(classifyMarket({ id: "TW:2330" })).toBe("TW");
    expect(classifyMarket({ id: "US:AAPL" })).toBe("US");
    expect(classifyMarket({ symbol: "2330.TW" })).toBe("TW");
    expect(classifyMarket({ symbol: "3081.TWO" })).toBe("TW");
    expect(classifyMarket({ symbol: "2454.tw" })).toBe("TW");
  });

  it("classifies exchange metadata and ordinary U.S. tickers", () => {
    expect(classifyMarket({ exchange: "TPEx", symbol: "3081" })).toBe("TW");
    expect(classifyMarket({ exchange: "TWSE", symbol: "2330" })).toBe("TW");
    expect(classifyMarket({ symbol: "NVDA" })).toBe("US");
    expect(classifyMarket({ symbol: "BRK.B" })).toBe("US");
  });

  it("does not treat a bare numeric code or junk id as a U.S. ticker", () => {
    expect(classifyMarket({ symbol: "2330" })).toBe("unknown");
    expect(classifyMarket({ id: "not-a-symbol" })).toBe("unknown");
    expect(classifyMarket({ symbol: "" })).toBe("unknown");
  });
});

describe("formatListingSymbol", () => {
  it("renders TWSE as .TW and TPEx as .TWO", () => {
    expect(formatListingSymbol({ market: "TW", symbol: "2330", exchange: "TWSE" })).toBe("2330.TW");
    expect(formatListingSymbol({ market: "TW", symbol: "2454", exchange: "TWSE" })).toBe("2454.TW");
    expect(formatListingSymbol({ market: "TW", symbol: "3081", exchange: "TPEx" })).toBe("3081.TWO");
  });

  it("leaves U.S. tickers unchanged and does not double a suffix", () => {
    expect(formatListingSymbol({ market: "US", symbol: "NVDA", exchange: "NASDAQ" })).toBe("NVDA");
    expect(formatListingSymbol({ market: "US", symbol: "MSFT" })).toBe("MSFT");
    expect(formatListingSymbol({ symbol: "2330.TW", exchange: "TWSE" })).toBe("2330.TW");
    expect(formatListingSymbol({ symbol: "3081.two", exchange: "TPEx" })).toBe("3081.TWO");
  });

  it("does not guess .TW when the Taiwan board is unknown", () => {
    expect(formatListingSymbol({ id: "TW:2330" })).toBe("2330");
  });
});

describe("partitionByMarket", () => {
  it("keeps original order inside each market", () => {
    const grouped = partitionByMarket(
      [
        { id: "US:MSFT" },
        { id: "TW:2330" },
        { id: "US:AAPL" },
        { id: "TW:3081" },
        { id: "mystery" },
      ],
      (item) => classifyMarket({ id: item.id }),
    );

    expect(grouped.TW.map((item) => item.id)).toEqual(["TW:2330", "TW:3081"]);
    expect(grouped.US.map((item) => item.id)).toEqual(["US:MSFT", "US:AAPL"]);
    expect(grouped.unknown.map((item) => item.id)).toEqual(["mystery"]);
  });
});
