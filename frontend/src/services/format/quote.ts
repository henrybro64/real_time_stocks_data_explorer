export function formatCurrency(value: number, currency: "USD" | "TWD") {
  const locale = currency === "USD" ? "en-US" : "zh-TW";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: currency === "USD" ? 2 : 2,
  }).format(value);
}

export function formatSignedNumber(value: number, currency: "USD" | "TWD") {
  const formatted = formatCurrency(Math.abs(value), currency);
  return `${value >= 0 ? "+" : "-"}${formatted}`;
}

export function formatPercent(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function formatLastUpdated(timestamp: string) {
  return new Intl.DateTimeFormat("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    month: "numeric",
    day: "numeric",
  }).format(new Date(timestamp));
}

export function formatMarketTime(timestamp: string, timezone: string) {
  return new Intl.DateTimeFormat("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    month: "numeric",
    day: "numeric",
    timeZone: timezone,
  }).format(new Date(timestamp));
}

