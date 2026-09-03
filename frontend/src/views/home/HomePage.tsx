"use client";

import { Column, Grid, InlineLoading, Tile } from "@carbon/react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useDeferredValue, useMemo, useRef, useState } from "react";
import type { MarketIndex, MarketStatus, QuoteResponse } from "@shared/api";
import type { Stock } from "@shared/stock";
import { useLanguage } from "../../app/i18n";
import { MarketIndicesStrip } from "../../components/market-status/MarketIndicesStrip";
import { MarketStatusStrip } from "../../components/market-status/MarketStatusStrip";
import { MarketSnapshot } from "../../components/market-snapshot/MarketSnapshot";
import { StockSearch } from "../../components/search/StockSearch";
import { WatchlistTable } from "../../components/watchlist/WatchlistTable";
import { useWatchlist } from "../../hooks/useWatchlist";
import { fetchMarketIndices, fetchMarkets, fetchQuotes, searchStocks } from "../../services/api/client";
import {
  isNullQuoteWarming,
  isQuoteWarmingTimedOut,
  QUOTE_WARMING_TIMEOUT_MS,
} from "../../services/market-data/quoteWarming";
import { resolveWatchlistQuoteQueryTiming } from "./watchlistQuoteQuery";
import { deriveWatchlistNoticeKind } from "./watchlistState";

const PENDING_BATCH_FRESHNESS = {
  cachedAt: null,
  ageSeconds: null,
  stale: true,
  source: "none" as const,
  refreshStatus: "queued" as const,
};

export function HomePage() {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const watchlist = useWatchlist();
  const warmingStartedAtByIdRef = useRef<Map<string, number>>(new Map());
  const [isRetryingQuotes, setIsRetryingQuotes] = useState(false);

  const marketsQuery = useQuery<MarketStatus[]>({
    queryKey: ["markets"],
    queryFn: fetchMarkets,
    refetchInterval: 60_000,
    staleTime: 60_000,
    refetchIntervalInBackground: false,
  });

  const marketIndicesQuery = useQuery<MarketIndex[]>({
    queryKey: ["market-indices"],
    queryFn: fetchMarketIndices,
    staleTime: 30_000,
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      const items = query.state.data;
      if (!items || items.length === 0) {
        return 60_000;
      }
      return items.some((item) => item.isMarketOpen) ? 30_000 : 120_000;
    },
  });

  const watchlistQuery = useQuery<QuoteResponse[]>({
    queryKey: ["watchlist-quotes", watchlist.ids],
    queryFn: () => fetchQuotes(watchlist.ids),
    enabled: watchlist.ids.length > 0,
    placeholderData: keepPreviousData,
    staleTime: (query) =>
      resolveWatchlistQuoteQueryTiming({
        stockIds: watchlist.ids,
        markets: marketsQuery.data,
        quotes: query.state.data,
        warmingTimedOut: allWarmingRowsTimedOut(query.state.data, warmingStartedAtByIdRef.current),
      }).staleTime,
    refetchInterval: (query) =>
      resolveWatchlistQuoteQueryTiming({
        stockIds: watchlist.ids,
        markets: marketsQuery.data,
        quotes: query.state.data,
        warmingTimedOut: allWarmingRowsTimedOut(query.state.data, warmingStartedAtByIdRef.current),
      }).refetchInterval,
    refetchIntervalInBackground: false,
    refetchOnMount: true,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  });

  const searchQuery = useQuery<Stock[]>({
    queryKey: ["search", deferredQuery],
    queryFn: () => searchStocks(deferredQuery),
    enabled: deferredQuery.trim().length > 0,
  });

  const warmingTimedOutById = useMemo(() => {
    const startedById = warmingStartedAtByIdRef.current;
    const now = Date.now();
    const next = new Map<string, boolean>();
    const activeIds = new Set<string>();

    for (const item of watchlistQuery.data ?? []) {
      if (!isNullQuoteWarming(item)) {
        startedById.delete(item.stock.id);
        next.set(item.stock.id, false);
        continue;
      }
      activeIds.add(item.stock.id);
      const startedAt = startedById.get(item.stock.id) ?? now;
      if (!startedById.has(item.stock.id)) {
        startedById.set(item.stock.id, startedAt);
      }
      next.set(item.stock.id, isQuoteWarmingTimedOut(startedAt, now, QUOTE_WARMING_TIMEOUT_MS));
    }

    for (const stockId of [...startedById.keys()]) {
      if (!activeIds.has(stockId)) {
        startedById.delete(stockId);
      }
    }
    return Object.fromEntries(next);
    // dataUpdatedAt ensures wall-clock timeout flips even when payload is unchanged.
  }, [watchlistQuery.data, watchlistQuery.dataUpdatedAt]);

  const watchlistItems = useMemo(() => {
    const quoteItemsById = new Map(
      (watchlistQuery.data ?? []).map((item) => [item.stock.id, item]),
    );
    const knownStocksById = new Map(
      (searchQuery.data ?? []).map((stock) => [stock.id, stock]),
    );

    return watchlist.ids.flatMap((stockId) => {
      const item = quoteItemsById.get(stockId);
      if (item) {
        return [item];
      }
      const stock = knownStocksById.get(stockId);
      if (stock) {
        // Keep single-quote fetch disabled: treat as batch warming placeholder.
        return [{ stock, quote: null, freshness: PENDING_BATCH_FRESHNESS }];
      }
      return [];
    });
  }, [searchQuery.data, watchlist.ids, watchlistQuery.data]);

  const pendingWatchlistIds = useMemo(() => {
    const hydratedIds = new Set(watchlistItems.map((item) => item.stock.id));
    return watchlist.ids.filter((stockId) => !hydratedIds.has(stockId));
  }, [watchlist.ids, watchlistItems]);

  const watchlistNoticeKind = useMemo(() => {
    return deriveWatchlistNoticeKind({
      hasLoaded: watchlist.hasLoaded,
      mutationError: watchlist.mutationError,
      queryError: watchlist.queryError,
      quoteError: watchlistQuery.error,
      quoteCount: watchlistQuery.data?.length ?? 0,
    });
  }, [
    watchlist.hasLoaded,
    watchlist.mutationError,
    watchlist.queryError,
    watchlistQuery.error,
    watchlistQuery.data,
  ]);

  const handleRetryQuotes = async () => {
    warmingStartedAtByIdRef.current.clear();
    setIsRetryingQuotes(true);
    try {
      await watchlistQuery.refetch();
    } finally {
      setIsRetryingQuotes(false);
    }
  };

  return (
    <Grid className="app-page-grid app-home-page-grid" fullWidth>
      <Column className="app-column-spacing app-home-hero-column" sm={4} md={8} lg={12}>
        <Tile className="app-hero-tile">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("home.hero.title")}</h1>
            <p className="app-body-copy app-hero-description">{t("home.hero.description")}</p>
          </div>
          <div className="app-hero-kpis">
            <span>
              {t("home.summary.markets")}: {marketsQuery.data?.length ?? 0}
            </span>
            <span>
              {t("home.summary.tracked")}: {watchlist.ids.length}
            </span>
            {(marketsQuery.isFetching || watchlistQuery.isFetching) && (
              <InlineLoading description={t("home.summary.loading")} status="active" />
            )}
          </div>
        </Tile>
      </Column>
      <Column
        className="app-column-spacing app-home-snapshot-column"
        id="market-snapshot"
        sm={4}
        md={8}
        lg={16}
      >
        <MarketSnapshot />
      </Column>
      <Column className="app-column-spacing app-home-markets-column" id="markets" sm={4} md={8} lg={16}>
        <MarketStatusStrip error={marketsQuery.error} isLoading={marketsQuery.isLoading} markets={marketsQuery.data ?? []} />
      </Column>
      <Column className="app-column-spacing app-home-indices-column" sm={4} md={8} lg={16}>
        <MarketIndicesStrip
          error={marketIndicesQuery.error}
          isLoading={marketIndicesQuery.isLoading}
          items={marketIndicesQuery.data ?? []}
        />
      </Column>
      <Column className="app-column-spacing app-home-search-column" sm={4} md={8} lg={16}>
        <StockSearch
          isSearching={searchQuery.isFetching}
          query={query}
          onQueryChange={setQuery}
          results={searchQuery.data ?? []}
          watchlistIds={watchlist.ids}
          onToggle={watchlist.toggle}
        />
      </Column>
      <Column className="app-column-spacing app-home-watchlist-column" id="watchlist" sm={4} md={8} lg={16}>
        <WatchlistTable
          isLoading={
            watchlist.ids.length === 0 &&
            !watchlistQuery.data &&
            (watchlistQuery.isLoading || watchlist.isLoading)
          }
          isRefreshing={!!watchlistQuery.data && (watchlistQuery.isFetching || watchlist.isMutating)}
          isRetryingQuote={isRetryingQuotes}
          items={watchlistItems}
          noticeKind={watchlistNoticeKind}
          pendingIds={pendingWatchlistIds}
          trackedCount={watchlist.ids.length}
          warmingTimedOutById={warmingTimedOutById}
          onRemove={watchlist.remove}
          onRetryQuote={handleRetryQuotes}
        />
      </Column>
    </Grid>
  );
}

function allWarmingRowsTimedOut(
  quotes: QuoteResponse[] | undefined,
  startedById: Map<string, number>,
): boolean {
  const now = Date.now();
  let anyWarming = false;
  for (const item of quotes ?? []) {
    if (!isNullQuoteWarming(item)) {
      continue;
    }
    anyWarming = true;
    const startedAt = startedById.get(item.stock.id) ?? now;
    if (!startedById.has(item.stock.id)) {
      startedById.set(item.stock.id, startedAt);
    }
    if (!isQuoteWarmingTimedOut(startedAt, now, QUOTE_WARMING_TIMEOUT_MS)) {
      return false;
    }
  }
  return anyWarming;
}
