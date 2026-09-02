import type { RealizedTradeCurrency } from "@shared/api";

export function formatRealizedCurrency(value: number, currency: RealizedTradeCurrency) {
  const prefix = currency === "TWD" ? "NT$" : "US$";
  return `${prefix}${value.toLocaleString(undefined, { maximumFractionDigits: 6 })}`;
}

export function formatSignedRealizedCurrency(value: number, currency: RealizedTradeCurrency) {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${formatRealizedCurrency(Math.abs(value), currency)}`;
}

export function formatSignedRealizedPercent(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}
