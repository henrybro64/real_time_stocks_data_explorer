import type { RealizedTradeCurrency } from "@shared/api";

function formatSign(value: number) {
  if (value > 0) {
    return "+";
  }
  if (value < 0) {
    return "-";
  }
  return "";
}

export function realizedCurrencyPrefix(currency: RealizedTradeCurrency) {
  return currency === "TWD" ? "NT$" : "US$";
}

export function formatRealizedCurrency(value: number, currency: RealizedTradeCurrency) {
  const prefix = realizedCurrencyPrefix(currency);
  return `${prefix}${value.toLocaleString(undefined, { maximumFractionDigits: 6 })}`;
}

export function formatSignedRealizedCurrency(value: number, currency: RealizedTradeCurrency) {
  return `${formatSign(value)}${formatRealizedCurrency(Math.abs(value), currency)}`;
}

export function formatSignedRealizedPercent(value: number) {
  return `${formatSign(value)}${Math.abs(value).toFixed(2)}%`;
}
