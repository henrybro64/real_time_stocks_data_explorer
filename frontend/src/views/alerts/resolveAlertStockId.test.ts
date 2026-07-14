import { describe, expect, it, vi } from "vitest";

import type { Stock } from "@shared/api";
import {
  AlertStockResolutionError,
  resolveAlertStockId,
} from "./resolveAlertStockId";

function stock(overrides: Partial<Stock> & Pick<Stock, "id" | "symbol" | "market">): Stock {
  return {
    name: overrides.symbol,
    exchange: "TEST",
    currency: overrides.market === "US" ? "USD" : "TWD",
    timezone: "UTC",
    nameEn: overrides.symbol,
    nameZhTw: overrides.symbol,
    ...overrides,
  };
}

describe("resolveAlertStockId", () => {
  it("normalizes canonical and bare U.S. tickers", async () => {
    const search = vi.fn(async () => [
      stock({ id: "US:AAPL", symbol: "AAPL", market: "US" }),
    ]);

    await expect(resolveAlertStockId("US:AAPL", search)).resolves.toBe("US:AAPL");
    await expect(resolveAlertStockId("AAPL", search)).resolves.toBe("US:AAPL");
    await expect(resolveAlertStockId("  aapl  ", search)).resolves.toBe("US:AAPL");
  });

  it("maps Taiwan numeric symbols to TW: prefix", async () => {
    const search = vi.fn(async () => []);
    await expect(resolveAlertStockId("2330", search)).resolves.toBe("TW:2330");
    expect(search).not.toHaveBeenCalled();
  });

  it("prefers US:SYMBOL when present among multi-market exact matches", async () => {
    const search = vi.fn(async () => [
      stock({ id: "US:FOO", symbol: "FOO", market: "US" }),
      stock({ id: "TW:1234", symbol: "FOO", market: "TW" }),
    ]);

    await expect(resolveAlertStockId("FOO", search)).resolves.toBe("US:FOO");
  });

  it("does not silently pick among ambiguous non-US exact symbol matches", async () => {
    const search = vi.fn(async () => [
      stock({ id: "TW:1111", symbol: "FOO", market: "TW" }),
      stock({ id: "TW:2222", symbol: "FOO", market: "TW" }),
    ]);

    await expect(resolveAlertStockId("FOO", search)).rejects.toMatchObject({
      code: "ambiguous",
      query: "FOO",
      choices: "TW:1111, TW:2222",
    });
    await expect(resolveAlertStockId("FOO", search)).rejects.toBeInstanceOf(
      AlertStockResolutionError,
    );
  });

  it("falls back to US:SYMBOL when search has no hits for a Latin ticker", async () => {
    const search = vi.fn(async () => []);
    await expect(resolveAlertStockId("MSFT", search)).resolves.toBe("US:MSFT");
  });

  it("rejects unsupported markets on canonical ids", async () => {
    const search = vi.fn(async () => []);
    await expect(resolveAlertStockId("XX:AAPL", search)).rejects.toMatchObject({
      code: "unknown",
      query: "XX:AAPL",
    });
    expect(search).not.toHaveBeenCalled();
  });
});
