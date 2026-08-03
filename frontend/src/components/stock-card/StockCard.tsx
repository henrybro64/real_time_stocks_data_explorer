"use client";

import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import { useQuery } from "@tanstack/react-query";
import type { ChartLayout, Quote, QuoteFreshnessMeta } from "@shared/quote";
import type { MarketCode, Stock } from "@shared/stock";
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
import {
  priceColorConventionFromMarket,
  priceStrokeColor,
  priceToneClassForMarket,
} from "../../services/market-data/priceTone";

type Props = {
  stock: Stock;
  /** `undefined` = hydrate via single-quote API; `null` = batch pending refresh. */
  quote?: Quote | null;
  freshness?: QuoteFreshnessMeta | null;
  isTracked: boolean;
  onToggle: (stockId: string) => void;
  /** Override Add / Remove button labels (e.g. portfolio vs watchlist). */
  addLabel?: string;
  removeLabel?: string;
};

export function StockCard({
  stock,
  quote: initialQuote,
  freshness: initialFreshness = null,
  isTracked,
  onToggle,
  addLabel,
  removeLabel,
}: Props) {
  const router = useRouter();
  const { language, t, formatNumber } = useLanguage();
  const quoteQuery = useQuery({
    queryKey: ["card-quote", stock.id],
    queryFn: async () => fetchQuote(stock.id),
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

  const quote =
    initialQuote === undefined ? quoteQuery.data?.quote ?? undefined : initialQuote ?? undefined;
  const freshness =
    initialQuote === undefined ? quoteQuery.data?.freshness ?? null : initialFreshness;
  const chartLayout = historyQuery.data?.chartLayout ?? null;
  const quoteRefreshFailed = initialQuote === undefined && quoteQuery.isError && quote != null;
  const detailHref = `/stock/${encodeURIComponent(stock.id)}`;
  const pendingUnavailable = quote == null && freshness?.refreshStatus === "unavailable";

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
            <p
              className={`${priceToneClassForMarket(stock.market, quote.change)} app-stock-card-change`}
              data-testid={`stock-card-change-${stock.id}`}
            >
              {formatSignedNumber(quote.change, stock.currency)} ({formatPercent(quote.changePercent)})
            </p>
          </div>
          <div className="app-stock-card-status-row">
            <QuoteFreshnessTag
              freshnessMeta={freshness}
              hasQuote
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
            market={stock.market}
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
      ) : pendingUnavailable ? (
        <div className="app-stock-card-status-row">
          <QuoteFreshnessTag
            freshnessMeta={freshness}
            hasQuote={false}
            isMarketOpen={false}
            timestamp=""
          />
          <RequestStateNotice compact kind="unavailable" scopeLabel={stock.symbol} />
        </div>
      ) : (
        <div className="app-stock-card-status-row">
          <QuoteFreshnessTag
            freshnessMeta={
              freshness ?? {
                cachedAt: null,
                ageSeconds: null,
                stale: true,
                source: "none",
                refreshStatus: "queued",
              }
            }
            hasQuote={false}
            isMarketOpen={false}
            timestamp=""
          />
          <InlineLoading description={t("stock.quoteRefreshing")} status="active" />
        </div>
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
  market,
  scopeLabel,
}: {
  chartLayout: ChartLayout | null;
  error?: unknown;
  isLoading: boolean;
  currency: "USD" | "TWD";
  market: MarketCode;
  scopeLabel: string;
}) {
  const { t } = useLanguage();
  const gradId = useId().replace(/:/g, "");
  const noticeKind = error ? (chartLayout ? "stale" : errorToNoticeKind(error)) : null;
  const convention = priceColorConventionFromMarket(market);

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
  const stroke = priceStrokeColor(convention, trendUp);
  const rangeToneClass = priceToneClassForMarket(market, trendUp ? 1 : -1);

  return (
    <div className="app-stock-card-sparkline">
      {noticeKind ? <RequestStateNotice compact kind={noticeKind} scopeLabel={scopeLabel} /> : null}
      <div className="app-stock-card-sparkline-header">
        <p className="app-section-meta">{t("stock.weekRange")}</p>
        <p className={`${rangeToneClass} app-stock-card-range`}>
          {formatCurrency(firstClose, currency)} {"->"} {formatCurrency(lastClose, currency)}
        </p>
      </div>
      <svg className="app-stock-card-sparkline-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
        <defs>
          <linearGradient id={gradId} x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <polygon points={`0,100 ${points} 100,100`} fill={`url(#${gradId})`} />
        <polyline
          data-testid={`stock-card-sparkline-stroke-${market}`}
          points={points}
          fill="none"
          stroke={stroke}
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}
