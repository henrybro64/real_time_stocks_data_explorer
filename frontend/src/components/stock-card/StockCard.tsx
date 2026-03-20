"use client";

import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import { useQuery } from "@tanstack/react-query";
import type { Candle, Quote } from "@shared/quote";
import type { Stock } from "@shared/stock";
import Link from "next/link";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { fetchHistory, fetchQuote } from "../../services/api/client";
import { formatCurrency, formatLastUpdated, formatPercent, formatSignedNumber } from "../../services/format/quote";

type Props = {
  stock: Stock;
  quote?: Quote;
  isTracked: boolean;
  onToggle: (stockId: string) => void;
};

export function StockCard({ stock, quote: initialQuote, isTracked, onToggle }: Props) {
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
    queryFn: async () => {
      const response = await fetchHistory(stock.id, "1W");
      return response.candles;
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const quote = initialQuote ?? quoteQuery.data;
  const chartCandles = historyQuery.data ?? [];

  return (
    <Tile className="app-stock-card">
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
          <Sparkline candles={chartCandles} currency={stock.currency} />
          <dl className="app-stock-card-meta">
            <div>
              <dt>{t("stock.market")}</dt>
              <dd>{stock.exchange}</dd>
            </div>
            <div>
              <dt>{t("stock.updatedAt")}</dt>
              <dd>{formatLastUpdated(quote.timestamp)}</dd>
            </div>
            <div>
              <dt>{t("stock.prevClose")}</dt>
              <dd>{formatCurrency(quote.previousClose, stock.currency)}</dd>
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
        <Button as={Link} kind="tertiary" size="sm" href={`/stock/${encodeURIComponent(stock.id)}`}>
          {t("stock.viewDetails")}
        </Button>
        <Button kind={isTracked ? "secondary" : "primary"} size="sm" onClick={() => onToggle(stock.id)}>
          {isTracked ? t("stock.remove") : t("stock.add")}
        </Button>
      </div>
    </Tile>
  );
}

function Sparkline({ candles, currency }: { candles: Candle[]; currency: "USD" | "TWD" }) {
  const { t } = useLanguage();

  if (candles.length < 2) {
    return (
      <div className="app-stock-card-sparkline app-stock-card-sparkline-empty">
        <p className="app-section-meta">{t("stock.weekRangeNoData")}</p>
      </div>
    );
  }

  const prices = candles.map((candle) => candle.close);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const start = prices[0];
  const end = prices[prices.length - 1];
  const trendUp = end >= start;
  const points = candles
    .map((candle, index) => {
      const x = (index / Math.max(candles.length - 1, 1)) * 100;
      const y = max === min ? 50 : 100 - ((candle.close - min) / (max - min)) * 100;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div className="app-stock-card-sparkline">
      <div className="app-stock-card-sparkline-header">
        <p className="app-section-meta">{t("stock.weekRange")}</p>
        <p className={trendUp ? "app-positive app-stock-card-range" : "app-negative app-stock-card-range"}>
          {formatCurrency(start, currency)} {"->"} {formatCurrency(end, currency)}
        </p>
      </div>
      <svg className="app-stock-card-sparkline-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
        <defs>
          <linearGradient id={`sparkline-fill-${currency}-${trendUp ? "up" : "down"}`} x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor={trendUp ? "#24a148" : "#da1e28"} stopOpacity="0.22" />
            <stop offset="100%" stopColor={trendUp ? "#24a148" : "#da1e28"} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <polygon
          points={`0,100 ${points} 100,100`}
          fill={`url(#sparkline-fill-${currency}-${trendUp ? "up" : "down"})`}
        />
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
