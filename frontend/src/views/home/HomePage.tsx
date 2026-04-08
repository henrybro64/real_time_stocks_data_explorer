"use client";

import { Column, Grid, InlineLoading, Tile } from "@carbon/react";
import { useDeferredValue, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { MarketIndex, MarketStatus, QuoteResponse } from "@shared/api";
import type { Stock } from "@shared/stock";
import { useLanguage } from "../../app/i18n";
import { MarketIndicesStrip } from "../../components/market-status/MarketIndicesStrip";
import { MarketStatusStrip } from "../../components/market-status/MarketStatusStrip";
import { StockSearch } from "../../components/search/StockSearch";
import { WatchlistTable } from "../../components/watchlist/WatchlistTable";
import { useWatchlist } from "../../hooks/useWatchlist";
import { fetchMarketIndices, fetchMarkets, fetchQuotes, searchStocks } from "../../services/api/client";

export function HomePage() {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const watchlist = useWatchlist();

  const marketsQuery = useQuery<MarketStatus[]>({
    queryKey: ["markets"],
    queryFn: fetchMarkets,
    refetchInterval: 60_000,
  });

  const marketIndicesQuery = useQuery<MarketIndex[]>({
    queryKey: ["market-indices"],
    queryFn: fetchMarketIndices,
    refetchInterval: (query) => {
      const items = query.state.data;
      if (!items || items.length === 0) {
        return 30_000;
      }
      return items.some((item) => item.isMarketOpen) ? 5_000 : 60_000;
    },
  });

  const watchlistQuery = useQuery<QuoteResponse[]>({
    queryKey: ["watchlist-quotes", watchlist.ids],
    queryFn: () => fetchQuotes(watchlist.ids),
    enabled: watchlist.ids.length > 0,
    refetchInterval: () => {
      const hasTaiwanStocks = watchlist.ids.some((id) => id.startsWith("TW:"));
      return hasTaiwanStocks ? 2_000 : 5_000;
    },
    refetchIntervalInBackground: true,
  });

  const searchQuery = useQuery<Stock[]>({
    queryKey: ["search", deferredQuery],
    queryFn: () => searchStocks(deferredQuery),
    enabled: deferredQuery.trim().length > 0,
  });

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
        return [{ stock }];
      }
      return [];
    });
  }, [searchQuery.data, watchlist.ids, watchlistQuery.data]);

  const pendingWatchlistIds = useMemo(() => {
    const hydratedIds = new Set(watchlistItems.map((item) => item.stock.id));
    return watchlist.ids.filter((stockId) => !hydratedIds.has(stockId));
  }, [watchlist.ids, watchlistItems]);

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={12}>
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
          </div>
        </Tile>
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={4}>
        <Tile className="app-summary-tile">
          <p className="app-eyebrow">{t("home.summary.title")}</p>
          <dl className="app-kpi-list">
            <div>
              <dt>{t("home.summary.markets")}</dt>
              <dd>{marketsQuery.data?.length ?? 0}</dd>
            </div>
            <div>
              <dt>{t("home.summary.tracked")}</dt>
              <dd>{watchlist.ids.length}</dd>
            </div>
          </dl>
          <div className="app-summary-loading-slot">
            {marketsQuery.isLoading || (!watchlistQuery.data && watchlistQuery.isLoading) ? (
              <InlineLoading description={t("home.summary.loading")} status="active" />
            ) : null}
          </div>
        </Tile>
      </Column>
      <Column className="app-column-spacing" id="markets" sm={4} md={8} lg={16}>
        <MarketStatusStrip isLoading={marketsQuery.isLoading} markets={marketsQuery.data ?? []} />
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <MarketIndicesStrip isLoading={marketIndicesQuery.isLoading} items={marketIndicesQuery.data ?? []} />
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <StockSearch
          isSearching={searchQuery.isFetching}
          query={query}
          onQueryChange={setQuery}
          results={searchQuery.data ?? []}
          watchlistIds={watchlist.ids}
          onToggle={watchlist.toggle}
        />
      </Column>
      <Column className="app-column-spacing" id="watchlist" sm={4} md={8} lg={16}>
        <WatchlistTable
          isLoading={
            watchlist.ids.length === 0
            && !watchlistQuery.data
            && (watchlistQuery.isLoading || watchlist.isLoading)
          }
          isRefreshing={!!watchlistQuery.data && (watchlistQuery.isFetching || watchlist.isMutating)}
          items={watchlistItems}
          pendingIds={pendingWatchlistIds}
          trackedCount={watchlist.ids.length}
          onRemove={watchlist.remove}
        />
      </Column>
    </Grid>
  );
}
