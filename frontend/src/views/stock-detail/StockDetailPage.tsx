"use client";

import { Column, Grid, InlineLoading, Tab, TabList, TabPanel, TabPanels, Tabs, Tile } from "@carbon/react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { HistoryResponse, KeyStatistics, NewsItem, QuoteResponse } from "@shared/api";
import type { TimeRange } from "@shared/quote";
import Link from "next/link";
import { useLanguage } from "../../app/i18n";
import { PriceChart } from "../../components/chart/PriceChart";
import { KeyStatisticsPanel } from "../../components/key-stats/KeyStatisticsPanel";
import { StockNewsPanel } from "../../components/news/StockNewsPanel";
import { QuoteSummary } from "../../components/quote/QuoteSummary";
import { fetchHistory, fetchKeyStatistics, fetchQuote, fetchStockNews } from "../../services/api/client";

const RANGES: TimeRange[] = ["1D", "1W", "1M", "3M", "6M", "1Y"];

type Props = {
  stockId?: string;
};

export function StockDetailPage({ stockId = "" }: Props) {
  const { t } = useLanguage();
  const decodedStockId = decodeURIComponent(stockId);
  const [range, setRange] = useState<TimeRange>("1D");
  const selectedIndex = RANGES.indexOf(range);

  const quoteQuery = useQuery<QuoteResponse>({
    queryKey: ["quote", decodedStockId],
    queryFn: () => fetchQuote(decodedStockId),
    enabled: decodedStockId.length > 0,
    refetchInterval: 5_000,
  });

  const historyQuery = useQuery<HistoryResponse>({
    queryKey: ["history", decodedStockId, range],
    queryFn: () => fetchHistory(decodedStockId, range),
    enabled: decodedStockId.length > 0,
  });

  const newsQuery = useQuery<NewsItem[]>({
    queryKey: ["stock-news", decodedStockId],
    queryFn: () => fetchStockNews(decodedStockId),
    enabled: decodedStockId.length > 0,
    refetchInterval: 60_000,
  });

  const keyStatsQuery = useQuery<KeyStatistics>({
    queryKey: ["key-statistics", decodedStockId],
    queryFn: () => fetchKeyStatistics(decodedStockId),
    enabled: decodedStockId.length > 0,
    staleTime: 60_000,
  });

  if (!decodedStockId) {
    return (
      <Grid className="app-page-grid" fullWidth>
        <Column sm={4} md={8} lg={16}>
          <Tile className="app-empty-state">{t("detail.invalidStockId")}</Tile>
        </Column>
      </Grid>
    );
  }

  if (!quoteQuery.data || !historyQuery.data) {
    return (
      <Grid className="app-page-grid" fullWidth>
        <Column sm={4} md={8} lg={16}>
          <Tile className="app-empty-state">
            <InlineLoading description={t("detail.loading")} status="active" />
          </Tile>
        </Column>
      </Grid>
    );
  }

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Link className="app-back-link" href="/">
          {t("detail.backToDashboard")}
        </Link>
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <QuoteSummary item={quoteQuery.data} />
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <Tabs
            onChange={({ selectedIndex: nextIndex }) => setRange(RANGES[nextIndex] ?? "1D")}
            selectedIndex={selectedIndex}
          >
            <TabList aria-label={t("detail.selectHistoryRange")} contained>
              {RANGES.map((item) => (
                <Tab key={item}>{item}</Tab>
              ))}
            </TabList>
            <TabPanels>
              {RANGES.map((item) => (
                <TabPanel key={item}>
                  <PriceChart
                    candles={historyQuery.data.candles}
                    chartLayout={historyQuery.data.chartLayout}
                    currency={historyQuery.data.stock.currency}
                    isActive={item === range}
                    range={item}
                  />
                </TabPanel>
              ))}
            </TabPanels>
          </Tabs>
        </Tile>
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <KeyStatisticsPanel
          currency={quoteQuery.data.stock.currency}
          isLoading={keyStatsQuery.isLoading}
          stats={keyStatsQuery.data ?? null}
        />
      </Column>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <StockNewsPanel isLoading={newsQuery.isLoading} items={newsQuery.data ?? []} />
      </Column>
    </Grid>
  );
}
