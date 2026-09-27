"use client";

import type { PriceLimitStatus } from "@shared/quote";
import type { CurrencyCode, MarketCode } from "@shared/stock";

import { useLanguage } from "../../app/i18n";
import { formatCurrency } from "../../services/format/quote";

type Props = {
  market: MarketCode;
  currency: CurrencyCode;
  lastPrice: number;
  priceLimitStatus?: PriceLimitStatus | null;
  /** Compact dashboard card vs the larger stock-detail price. */
  variant: "card" | "detail";
  testId: string;
};

export function LimitPrice({
  market,
  currency,
  lastPrice,
  priceLimitStatus,
  variant,
  testId,
}: Props) {
  const { t } = useLanguage();
  const formatted = formatCurrency(lastPrice, currency);
  const priceClass = variant === "card" ? "app-stock-card-price" : "app-detail-price";
  const status = market === "TW" ? priceLimitStatus : null;
  const limited = status === "limit_up" || status === "limit_down";

  if (!limited) {
    return (
      <p className={priceClass} data-testid={testId}>
        {formatted}
      </p>
    );
  }

  const label = t(status === "limit_up" ? "quote.limitUp" : "quote.limitDown");
  const tone = status === "limit_up" ? "up" : "down";

  return (
    <div
      aria-label={`${formatted}, ${label}`}
      className={`app-price-limit app-price-limit--${variant} app-price-limit--${tone}`}
      data-testid={testId}
      role="group"
    >
      <p aria-hidden="true" className={`${priceClass} app-price-limit-pill`}>
        {formatted}
      </p>
      <span aria-hidden="true" className="app-price-limit-label" data-testid={`${testId}-label`}>
        {label}
      </span>
    </div>
  );
}
