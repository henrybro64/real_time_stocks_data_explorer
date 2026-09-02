import { describe, expect, it } from "vitest";
import type { RealizedTrade } from "@shared/api";
import { buildSampleRealizedTrade } from "./realizedTradeTestFixtures";
import {
  buildMonthlyRealizedGainSeries,
  isSellDateInPeriod,
  isSellDateOnOrBeforeToday,
  parseDateOnly,
  previewRealizedGain,
  subtractCalendarMonths,
  sumRealizedGainForPeriod,
  tradesMatchCurrency,
} from "./realizedGainCalculations";

function trade(overrides: Partial<RealizedTrade> = {}): RealizedTrade {
  return buildSampleRealizedTrade({
    symbol: "AAPL",
    currency: "USD",
    buyPrice: 100,
    sellPrice: 120,
    shares: 1,
    sellDate: "2026-08-15",
    realizedGain: 20,
    realizedReturnPercent: 20,
    ...overrides,
  });
}

describe("realizedGainCalculations", () => {
  const today = new Date(2026, 8, 2);

  it("uses sellDate for period totals and excludes trades before the window", () => {
    const trades = [
      trade({ sellDate: "2026-08-15", realizedGain: 100 }),
      trade({ sellDate: "2026-06-15", realizedGain: 50 }),
    ];
    expect(sumRealizedGainForPeriod(trades, "1M", today)).toBe(100);
    expect(sumRealizedGainForPeriod(trades, "3M", today)).toBe(150);
  });

  it("excludes future sell dates from period totals", () => {
    const trades = [
      trade({ sellDate: "2026-12-31", realizedGain: 999 }),
      trade({ sellDate: "2026-08-15", realizedGain: 100 }),
    ];
    expect(sumRealizedGainForPeriod(trades, "1Y", today)).toBe(100);
    expect(isSellDateInPeriod("2026-12-31", "1Y", today)).toBe(false);
  });

  it("includes trades exactly on the period boundary", () => {
    const start = subtractCalendarMonths(today, 1);
    const boundary = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}-${String(start.getDate()).padStart(2, "0")}`;
    const trades = [trade({ sellDate: boundary, realizedGain: 42 })];
    expect(isSellDateInPeriod(boundary, "1M", today)).toBe(true);
    expect(sumRealizedGainForPeriod(trades, "1M", today)).toBe(42);
  });

  it("clamps month-end subtraction for March 31 minus one month", () => {
    const result = subtractCalendarMonths(new Date(2026, 2, 31), 1);
    expect(result.getMonth()).toBe(1);
    expect(result.getDate()).toBe(28);
  });

  it("builds twelve ordered monthly buckets with zero-filled empty months", () => {
    const series = buildMonthlyRealizedGainSeries(
      [trade({ sellDate: "2026-08-15", realizedGain: 30 })],
      today,
    );
    expect(series).toHaveLength(12);
    expect(series[0].monthKey < series[11].monthKey).toBe(true);
    expect(series.filter((point) => point.totalGain === 0).length).toBeGreaterThan(0);
    expect(series.find((point) => point.monthKey === "2026-08")?.totalGain).toBe(30);
  });

  it("excludes future sell dates from the monthly chart series", () => {
    const series = buildMonthlyRealizedGainSeries(
      [trade({ sellDate: "2026-09-15", realizedGain: 999 })],
      today,
    );
    expect(series.find((point) => point.monthKey === "2026-09")?.totalGain).toBe(0);
    expect(isSellDateOnOrBeforeToday("2026-09-15", today)).toBe(false);
  });

  it("sums multiple trades in the same month and keeps negative totals", () => {
    const series = buildMonthlyRealizedGainSeries(
      [
        trade({ sellDate: "2026-08-01", realizedGain: 10 }),
        trade({ sellDate: "2026-08-20", realizedGain: -25 }),
      ],
      today,
    );
    expect(series.find((point) => point.monthKey === "2026-08")?.totalGain).toBe(-15);
  });

  it("parses date-only strings without timezone shifts", () => {
    const parsed = parseDateOnly("2026-08-20");
    expect(parsed.getFullYear()).toBe(2026);
    expect(parsed.getMonth()).toBe(7);
    expect(parsed.getDate()).toBe(20);
  });

  it("calculates preview gain and return", () => {
    expect(previewRealizedGain(180.25, 195.5, 10)).toEqual({
      realizedGain: 152.5,
      realizedReturnPercent: 8.4605,
    });
  });

  it("detects mixed-currency datasets", () => {
    expect(tradesMatchCurrency([trade({ currency: "USD" })], "USD")).toBe(true);
    expect(
      tradesMatchCurrency(
        [trade({ currency: "USD" }), trade({ id: 2, currency: "TWD" })],
        "USD",
      ),
    ).toBe(false);
  });
});
