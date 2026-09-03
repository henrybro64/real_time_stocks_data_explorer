import type { RealizedTrade } from "@shared/api";

export function buildSampleRealizedTrade(
  overrides: Partial<RealizedTrade> = {},
): RealizedTrade {
  return {
    id: 1,
    symbol: "2330",
    currency: "TWD",
    buyPrice: 920,
    sellPrice: 1020,
    shares: 1000,
    buyDate: null,
    sellDate: "2026-08-01",
    cumulativeDividends: 0,
    priceGain: 100000,
    realizedGain: 100000,
    realizedReturnPercent: 10.8696,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}
