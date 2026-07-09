"use client";

import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import { useQuery } from "@tanstack/react-query";
import type { ChartLayout, Quote } from "@shared/quote";
import type { Stock } from "@shared/stock";
import { useId, type KeyboardEvent, type MouseEvent } from "react";
import { useRouter } from "next/navigation";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { fetchHistory, fetchQuote } from "../../services/api/client";
import { QuoteFreshnessTag } from "../state/QuoteFreshnessTag";
import { RequestStateNotice, errorToNoticeKind } from "../state/RequestStateNotice";
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
  const router = useRouter();
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
  const quoteRefreshFailed = initialQuote === undefined && quoteQuery.isError && quote != null;
  const detailHref = `/stock/${encodeURIComponent(stock.id)}`;

  const handleCardClick = (event: MouseEvent<HTMLDivElement>) => {
    if (shouldIgnoreNavigation(event.target, event.currentTarget)) {
      return;
    }
    router.push(detailHref);
  };

  const handleCardKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) {
      return;
    }
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    router.push(detailHref);
  };

  return (
    <Tile
      aria-label={`${getDisplayStockName(stock, language)} ${t("stock.viewDetails")}`}
      className="app-stock-card app-stock-card-link"
      data-testid={`stock-card-${stock.id}`}
      onClick={handleCardClick}
      onKeyDown={handleCardKeyDown}
      role="link"
      tabIndex={0}
    >
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
          <div className="app-stock-card-status-row">
            <QuoteFreshnessTag
              isMarketOpen={quote.isMarketOpen}
              refreshFailed={quoteRefreshFailed}
              timestamp={quote.timestamp}
            />
            <p className="app-section-meta">
              {stock.market === "TW"
                ? `${t("stock.updatedAt")} ${formatLastUpdatedWithSeconds(quote.timestamp)}`
                : `${t("stock.updatedAt")} ${formatLastUpdated(quote.timestamp)}`}
            </p>
          </div>
          {quoteRefreshFailed ? (
            <RequestStateNotice compact kind="stale" scopeLabel={stock.symbol} />
          ) : null}
          <Sparkline
            chartLayout={chartLayout}
            currency={stock.currency}
            error={historyQuery.error}
            isLoading={historyQuery.isLoading}
            scopeLabel={`${stock.symbol} ${t("stock.weekRange")}`}
          />
          <dl className="app-stock-card-meta">
            <div>
              <dt>{t("stock.market")}</dt>
              <dd>{stock.exchange}</dd>
            </div>
            <div>
              <dt>{t("stock.updatedAt")}</dt>
              <dd>{stock.market === "TW" ? formatLastUpdatedWithSeconds(quote.timestamp) : formatLastUpdated(quote.timestamp)}</dd>
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
        <RequestStateNotice compact kind={errorToNoticeKind(quoteQuery.error)} scopeLabel={stock.symbol} />
      ) : (
        <InlineLoading description={t("stock.quoteLoading")} status="active" />
      )}

      <div className="app-stock-card-actions">
        <Button
          data-testid={`stock-card-view-${stock.id}`}
          href={detailHref}
          kind="tertiary"
          size="sm"
        >
          {t("stock.viewDetails")}
        </Button>
        <Button
          data-testid={`stock-card-alert-${stock.id}`}
          href={`/alerts?stockId=${encodeURIComponent(stock.id)}`}
          kind="ghost"
          size="sm"
        >
          {t("detail.createAlert")}
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

function shouldIgnoreNavigation(target: EventTarget | null, currentTarget: EventTarget | null) {
  if (!(target instanceof HTMLElement) || !(currentTarget instanceof HTMLElement)) {
    return false;
  }
  const interactive = target.closest("button, a, input, select, textarea, summary, [role='button']");
  return Boolean(interactive && interactive !== currentTarget && currentTarget.contains(interactive));
}

function Sparkline({
  chartLayout,
  error,
  isLoading,
  currency,
  scopeLabel,
}: {
  chartLayout: ChartLayout | null;
  error?: unknown;
  isLoading: boolean;
  currency: "USD" | "TWD";
  scopeLabel: string;
}) {
  const { t } = useLanguage();
  const gradId = useId().replace(/:/g, "");
  const noticeKind = error ? (chartLayout ? "stale" : errorToNoticeKind(error)) : null;

  if (isLoading) {
    return (
      <div className="app-stock-card-sparkline app-stock-card-sparkline-empty">
        <InlineLoading description={t("stock.weekRange")} status="active" />
      </div>
    );
  }

  if ((!chartLayout || chartLayout.points.length < 2) && noticeKind) {
    return <RequestStateNotice compact kind={noticeKind} scopeLabel={scopeLabel} />;
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
      {noticeKind ? <RequestStateNotice compact kind={noticeKind} scopeLabel={scopeLabel} /> : null}
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
