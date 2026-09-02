import type { RealizedTrade, RealizedTradeCurrency } from "@shared/api";

export type RealizedGainPeriod = "1M" | "3M" | "6M" | "1Y";

export type MonthlyRealizedGainPoint = {
  monthKey: string;
  label: string;
  totalGain: number;
};

export function parseDateOnly(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function formatDateOnly(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function isSellDateOnOrBeforeToday(sellDate: string, today: Date): boolean {
  return parseDateOnly(sellDate) <= startOfDay(today);
}

export function subtractCalendarMonths(date: Date, months: number): Date {
  const result = new Date(date);
  const originalDay = result.getDate();
  result.setDate(1);
  result.setMonth(result.getMonth() - months);
  const lastDay = new Date(result.getFullYear(), result.getMonth() + 1, 0).getDate();
  result.setDate(Math.min(originalDay, lastDay));
  return result;
}

export function periodStartDate(period: RealizedGainPeriod, today: Date): Date {
  switch (period) {
    case "1M":
      return subtractCalendarMonths(today, 1);
    case "3M":
      return subtractCalendarMonths(today, 3);
    case "6M":
      return subtractCalendarMonths(today, 6);
    case "1Y":
      return subtractCalendarMonths(today, 12);
    default:
      return today;
  }
}

export function isSellDateInPeriod(
  sellDate: string,
  period: RealizedGainPeriod,
  today: Date,
): boolean {
  const sell = parseDateOnly(sellDate);
  const start = startOfDay(periodStartDate(period, today));
  const todayOnly = startOfDay(today);
  return sell >= start && sell <= todayOnly;
}

export function sumRealizedGainForPeriod(
  trades: RealizedTrade[],
  period: RealizedGainPeriod,
  today: Date,
): number {
  return trades.reduce((total, trade) => {
    if (!isSellDateInPeriod(trade.sellDate, period, today)) {
      return total;
    }
    return total + trade.realizedGain;
  }, 0);
}

export function previewRealizedGain(
  buyPrice: number,
  sellPrice: number,
  shares: number,
): { realizedGain: number; realizedReturnPercent: number } | null {
  if (
    !Number.isFinite(buyPrice) ||
    !Number.isFinite(sellPrice) ||
    !Number.isFinite(shares) ||
    buyPrice <= 0 ||
    sellPrice <= 0 ||
    shares <= 0
  ) {
    return null;
  }
  const realizedGain = (sellPrice - buyPrice) * shares;
  const realizedReturnPercent = ((sellPrice - buyPrice) / buyPrice) * 100;
  return {
    realizedGain: Number(realizedGain.toFixed(2)),
    realizedReturnPercent: Number(realizedReturnPercent.toFixed(4)),
  };
}

export function buildMonthlyRealizedGainSeries(
  trades: RealizedTrade[],
  today: Date,
): MonthlyRealizedGainPoint[] {
  const buckets: MonthlyRealizedGainPoint[] = [];
  for (let offset = 11; offset >= 0; offset -= 1) {
    const monthDate = subtractCalendarMonths(today, offset);
    const monthKey = `${monthDate.getFullYear()}-${String(monthDate.getMonth() + 1).padStart(2, "0")}`;
    const label = monthKey;
    const totalGain = trades.reduce((sum, trade) => {
      if (!isSellDateOnOrBeforeToday(trade.sellDate, today)) {
        return sum;
      }
      const sellDate = parseDateOnly(trade.sellDate);
      if (
        sellDate.getFullYear() === monthDate.getFullYear() &&
        sellDate.getMonth() === monthDate.getMonth()
      ) {
        return sum + trade.realizedGain;
      }
      return sum;
    }, 0);
    buckets.push({ monthKey, label, totalGain });
  }
  return buckets;
}

export function tradesMatchCurrency(
  trades: RealizedTrade[],
  currency: RealizedTradeCurrency,
): boolean {
  return trades.every((trade) => trade.currency === currency);
}
