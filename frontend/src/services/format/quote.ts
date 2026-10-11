function currencyFractionDigits(value: number): { minimumFractionDigits: number; maximumFractionDigits: number } {
  // Match backend quote rounding: four decimals under 1, otherwise two (#275).
  if (Math.abs(value) < 1) {
    return { minimumFractionDigits: 2, maximumFractionDigits: 4 };
  }
  return { minimumFractionDigits: 2, maximumFractionDigits: 2 };
}

export function formatCurrency(value: number, currency: "USD" | "TWD") {
  const locale = currency === "USD" ? "en-US" : "zh-TW";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    ...currencyFractionDigits(value),
  }).format(value);
}

export function formatSignedNumber(value: number, currency: "USD" | "TWD") {
  const formatted = formatCurrency(Math.abs(value), currency);
  return `${value >= 0 ? "+" : "-"}${formatted}`;
}

export function formatPercent(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function formatLastUpdated(timestamp: string, locale: string = "zh-TW") {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    month: "numeric",
    day: "numeric",
  }).format(new Date(timestamp));
}

export function formatLastUpdatedWithSeconds(timestamp: string, locale: string = "zh-TW") {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
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

