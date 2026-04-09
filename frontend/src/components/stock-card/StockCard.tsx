"use client";

import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import { useQuery } from "@tanstack/react-query";
import type { ChartLayout, Quote } from "@shared/quote";
import type { Stock } from "@shared/stock";
import { useId } from "react";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { fetchHistory, fetchQuote } from "../../services/api/client";
import {
  formatCurrency,
  formatLastUpdated,
  formatLastUpdatedWithSeconds,
  formatPercent,
  formatSignedNumber,
} from "../../services/format/quote";

type Props = {
  stock: Stock;
  quote?: Quote;
  isTracked: boolean;
  onToggle: (stockId: string) => void;
  /** Override Add / Remove button labels (e.g. portfolio vs watchlist). */
  addLabel?: string;
  removeLabel?: string;
};

export function StockCard({
  stock,
  quote: initialQuote,
  isTracked,
  onToggle,
  addLabel,
  removeLabel,
}: Props) {
  const { language, t, formatNumber } = useLanguage();
  const quoteQuery = useQuery({
    queryKey: ["card-quote", stock.id],
    queryFn: async () => {
      const response = await fetchQuote(stock.id);
      return response.quote;
    },
    enabled: initialQuote === undefined,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const historyQuery = useQuery({
    queryKey: ["card-history", stock.id, "1W"],
    queryFn: () => fetchHistory(stock.id, "1W"),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const quote = initialQuote ?? quoteQuery.data;
  const chartLayout = historyQuery.data?.chartLayout ?? null;

  return (
    <Tile className="app-stock-card" data-testid={`stock-card-${stock.id}`}>
      <div className="app-stock-card-top">
        <div>
          <p className="app-result-symbol">
            {stock.symbol} <span>{stock.exchange}</span>
          </p>
          <p className="app-result-name">{getDisplayStockName(stock, language)}</p>
        </div>
        <Tag type="cool-gray">{stock.currency}</Tag>
      </div>

      {quote ? (
        <>
          <div className="app-stock-card-price-block">
            <p className="app-stock-card-price">{formatCurrency(quote.lastPrice, stock.currency)}</p>
            <p className={quote.change >= 0 ? "app-positive app-stock-card-change" : "app-negative app-stock-card-change"}>
              {formatSignedNumber(quote.change, stock.currency)} ({formatPercent(quote.changePercent)})
            </p>
          </div>
          <Sparkline chartLayout={chartLayout} isLoading={historyQuery.isLoading} currency={stock.currency} />
          <dl className="app-stock-card-meta">
            <div>
              <dt>{t("stock.market")}</dt>
              <dd>{stock.exchange}</dd>
            </div>
            <div>
              <dt>{t("stock.updatedAt")}</dt>
              <dd>
                {stock.market === "TW"
                  ? formatLastUpdatedWithSeconds(quote.timestamp)
                  : formatLastUpdated(quote.timestamp)}
              </dd>
            </div>
            <div>
              <dt>{t("stock.prevClose")}</dt>
              <dd>{formatCurrency(quote.previousClose, stock.currency)}</dd>
            </div>
            <div>
              <dt>{t("quote.low")}</dt>
              <dd>{quote.low != null ? formatCurrency(quote.low, stock.currency) : "-"}</dd>
            </div>
            <div>
              <dt>{t("quote.high")}</dt>
              <dd>{quote.high != null ? formatCurrency(quote.high, stock.currency) : "-"}</dd>
            </div>
            <div>
              <dt>{t("stock.volume")}</dt>
              <dd>{quote.volume ? formatNumber(quote.volume) : "-"}</dd>
            </div>
          </dl>
        </>
      ) : quoteQuery.isError ? (
        <p className="app-supporting-copy">{t("stock.quoteLoadError")}</p>
      ) : (
        <InlineLoading description={t("stock.quoteLoading")} status="active" />
      )}

      <div className="app-stock-card-actions">
        <Button
          data-testid={`stock-card-view-${stock.id}`}
          href={`/stock/${encodeURIComponent(stock.id)}`}
          kind="tertiary"
          size="sm"
        >
          {t("stock.viewDetails")}
        </Button>
        <Button
          data-testid={`stock-card-toggle-${stock.id}`}
          kind={isTracked ? "secondary" : "primary"}
          size="sm"
          onClick={() => onToggle(stock.id)}
        >
          {isTracked ? (removeLabel ?? t("stock.remove")) : (addLabel ?? t("stock.add"))}
        </Button>
      </div>
    </Tile>
  );
}

function Sparkline({
  chartLayout,
  isLoading,
  currency,
}: {
  chartLayout: ChartLayout | null;
  isLoading: boolean;
  currency: "USD" | "TWD";
}) {
  const { t } = useLanguage();
  const gradId = useId().replace(/:/g, "");

  if (isLoading) {
    return (
      <div className="app-stock-card-sparkline app-stock-card-sparkline-empty">
        <InlineLoading description={t("stock.weekRange")} status="active" />
      </div>
    );
  }

  if (!chartLayout || chartLayout.points.length < 2) {
    return (
      <div className="app-stock-card-sparkline app-stock-card-sparkline-empty">
        <p className="app-section-meta">{t("stock.weekRangeNoData")}</p>
      </div>
    );
  }

  const { pointsPolyline, trendUp, firstClose, lastClose } = chartLayout;
  const points = pointsPolyline;

  return (
    <div className="app-stock-card-sparkline">
      <div className="app-stock-card-sparkline-header">
        <p className="app-section-meta">{t("stock.weekRange")}</p>
        <p className={trendUp ? "app-positive app-stock-card-range" : "app-negative app-stock-card-range"}>
          {formatCurrency(firstClose, currency)} {"->"} {formatCurrency(lastClose, currency)}
        </p>
      </div>
      <svg className="app-stock-card-sparkline-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
        <defs>
          <linearGradient id={gradId} x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor={trendUp ? "#24a148" : "#da1e28"} stopOpacity="0.22" />
            <stop offset="100%" stopColor={trendUp ? "#24a148" : "#da1e28"} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <polygon points={`0,100 ${points} 100,100`} fill={`url(#${gradId})`} />
        <polyline
          points={points}
          fill="none"
          stroke={trendUp ? "#24a148" : "#da1e28"}
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}
