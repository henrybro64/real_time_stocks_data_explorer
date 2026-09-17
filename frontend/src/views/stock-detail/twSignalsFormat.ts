type TwSignalLanguage = "en" | "zh-TW";

export function buildDateRangeLabel(
  history: Array<{ date: string }>,
  language: TwSignalLanguage,
): string {
  if (history.length === 0) {
    return "";
  }
  const start = formatDisplayDate(history[0]?.date ?? null, language);
  const end = formatDisplayDate(history[history.length - 1]?.date ?? null, language);
  return `${start} - ${end}`;
}

export function formatDisplayDate(
  rawDate: string | null,
  language: TwSignalLanguage,
): string {
  if (!rawDate) {
    return "—";
  }
  const date = new Date(`${rawDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return rawDate;
  }
  return new Intl.DateTimeFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function formatAxisDate(rawDate: string, language: TwSignalLanguage): string {
  if (!rawDate) {
    return "—";
  }
  const date = new Date(`${rawDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return rawDate;
  }
  return new Intl.DateTimeFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    month: "numeric",
    day: "numeric",
  }).format(date);
}

export function formatSignedMetric(
  value: number | null | undefined,
  formatNumber: (value: number) => string,
  unit: string,
): string {
  if (value == null) {
    return "—";
  }
  const absoluteValue = formatLocalizedMetric(Math.abs(value), formatNumber);
  if (value > 0) {
    return `+${absoluteValue} ${unit}`;
  }
  if (value < 0) {
    return `-${absoluteValue} ${unit}`;
  }
  return `${absoluteValue} ${unit}`;
}

export function formatUnsignedMetric(
  value: number | null | undefined,
  formatNumber: (value: number) => string,
  unit: string,
): string {
  if (value == null) {
    return "—";
  }
  return `${formatLocalizedMetric(value, formatNumber)} ${unit}`;
}

function formatLocalizedMetric(
  value: number,
  formatNumber: (value: number) => string,
): string {
  if (Number.isInteger(value)) {
    return formatNumber(value);
  }
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 3,
  }).format(value);
}

export function twMarketToneClass(value: number | null | undefined): string {
  if (value == null) {
    return "app-tw-market-neutral";
  }
  if (value > 0) {
    return "app-tw-market-positive";
  }
  if (value < 0) {
    return "app-tw-market-negative";
  }
  return "app-tw-market-neutral";
}
