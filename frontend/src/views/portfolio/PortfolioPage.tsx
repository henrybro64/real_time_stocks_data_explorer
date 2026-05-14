"use client";

import {
  Column,
  Grid,
  InlineLoading,
  Button,
  Tag,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
  Tile,
} from "@carbon/react";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { PortfolioAnalysisResponse } from "@shared/api";
import type { TimeRange } from "@shared/quote";
import type { Stock } from "@shared/stock";
import Link from "next/link";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { HistoricalValueChart } from "../../components/chart/HistoricalValueChart";
import { getHistoryZoomState, stepHistoryRange } from "../../components/chart/historyRange";
import { StockSearch } from "../../components/search/StockSearch";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { usePortfolioPositions } from "../../hooks/usePortfolioPositions";
import {
  analyzePortfolio,
  fetchPortfolioHoldingsHistory,
  fetchPortfolioSummary,
  fetchUsdTwdFxRate,
  searchStocks,
} from "../../services/api/client";
import { formatCurrency, formatLastUpdated, formatPercent, formatSignedNumber } from "../../services/format/quote";

type DisplayCurrency = "USD" | "TWD";

const AI_NOT_CONFIGURED_MESSAGE = "AI is not configured. Please set up your AI provider in AI Settings.";

function toDisplayCurrency(currency: string): DisplayCurrency | null {
  return currency === "USD" || currency === "TWD" ? currency : null;
}

export function PortfolioPage() {
  const { t, language, formatNumber } = useLanguage();
  const [query, setQuery] = useState("");
  const [historyRange, setHistoryRange] = useState<TimeRange>("1M");
  const [draftQuantities, setDraftQuantities] = useState<Record<number, string>>({});
  const [draftAverageCosts, setDraftAverageCosts] = useState<Record<number, string>>({});
  const [savingPositionIds, setSavingPositionIds] = useState<Record<number, boolean>>({});
  const [savedPositionIds, setSavedPositionIds] = useState<Record<number, boolean>>({});
  const portfolio = usePortfolioPositions();
  const analysisMutation = useMutation<PortfolioAnalysisResponse>({
    mutationFn: analyzePortfolio,
    retry: false,
  });

  const trimmedSearch = query.trim();
  const searchQuery = useQuery<Stock[]>({
    queryKey: ["portfolio-search", trimmedSearch],
    queryFn: () => searchStocks(trimmedSearch),
    enabled: trimmedSearch.length > 0,
  });
  const summaryQuery = useQuery({
    queryKey: ["portfolio-summary"],
    queryFn: fetchPortfolioSummary,
    refetchInterval: 5000,
  });
  const items = portfolio.positions;
  const fxQuery = useQuery({
    queryKey: ["usd-twd-fx-rate"],
    queryFn: fetchUsdTwdFxRate,
    refetchInterval: 60_000,
    enabled: items.length > 0,
  });
  const holdingsHistoryQuery = useQuery({
    queryKey: ["portfolio-holdings-history", historyRange],
    queryFn: () => fetchPortfolioHoldingsHistory(historyRange),
    enabled: items.length > 0,
    refetchInterval: 60_000,
  });
  /** While the first fetch runs, `data` is undefined so `items` is [] — do not treat as empty. */
  const showPortfolioLoading = portfolio.isLoading && items.length === 0;
  const showPortfolioError = portfolio.queryError != null && items.length === 0;
  const isEmpty = !portfolio.isLoading && portfolio.queryError == null && items.length === 0;
  const portfolioTableNoticeKind =
    portfolio.queryError != null ? (items.length > 0 ? "stale" : errorToNoticeKind(portfolio.queryError)) : null;
  const portfolioActionNoticeKind =
    portfolio.mutationError != null
      ? errorToNoticeKind(portfolio.mutationError)
      : portfolio.actionError != null
        ? errorToNoticeKind(portfolio.actionError)
        : null;
  const portfolioAggregateNoticeKind =
    summaryQuery.error != null
      ? (summaryQuery.data ? "stale" : errorToNoticeKind(summaryQuery.error))
      : fxQuery.error != null
        ? (fxQuery.data ? "stale" : errorToNoticeKind(fxQuery.error))
        : null;
  const holdingsHistoryNoticeKind =
    holdingsHistoryQuery.error != null
      ? (holdingsHistoryQuery.data ? "stale" : errorToNoticeKind(holdingsHistoryQuery.error))
      : null;

  /** Sum table `marketValue` by native currency — matches the grid; avoids $0 while /summary is slow. */
  const holdingsNativeTotals = useMemo(() => {
    let usd = 0;
    let twd = 0;
    for (const p of items) {
      const c = p.stock.currency;
      if (c === "USD") {
        usd += p.marketValue;
      } else if (c === "TWD") {
        twd += p.marketValue;
      }
    }
    return { totalUsd: usd, totalTwd: twd };
  }, [items]);

  const usdTwdRate =
    summaryQuery.data != null && summaryQuery.data.usdTwdRate > 0
      ? summaryQuery.data.usdTwdRate
      : fxQuery.data?.rate ?? 0;

  const totalUsd = holdingsNativeTotals.totalUsd;
  const totalTwd = holdingsNativeTotals.totalTwd;
  const combinedUsd =
    usdTwdRate > 0 ? totalUsd + totalTwd / usdTwdRate : totalUsd;
  const combinedTwd =
    usdTwdRate > 0 ? totalTwd + totalUsd * usdTwdRate : totalTwd;
  const positionIdByStockId = new Map(items.map((item) => [item.stockId, item.id]));
  const { canZoomIn, canZoomOut } = getHistoryZoomState(historyRange);
  const positionsVersion = items
    .map((item) => `${item.id}:${item.quantity}:${item.averageCost}`)
    .join("|");

  useEffect(() => {
    analysisMutation.reset();
  }, [positionsVersion]);

  const handleQuantityDraftChange = (positionId: number, nextValue: string) => {
    setDraftQuantities((current) => ({ ...current, [positionId]: nextValue }));
  };

  const handleAverageCostDraftChange = (positionId: number, nextValue: string) => {
    setDraftAverageCosts((current) => ({ ...current, [positionId]: nextValue }));
  };

  const resetQuantityDraft = (positionId: number) => {
    setDraftQuantities((current) => {
      const next = { ...current };
      delete next[positionId];
      return next;
    });
  };

  const resetAverageCostDraft = (positionId: number) => {
    setDraftAverageCosts((current) => {
      const next = { ...current };
      delete next[positionId];
      return next;
    });
  };

  const submitPositionUpdate = (
    positionId: number,
    payload: { quantity?: number; averageCost?: number },
  ) => {
    setSavingPositionIds((current) => ({ ...current, [positionId]: true }));
    setSavedPositionIds((current) => ({ ...current, [positionId]: false }));
    portfolio.update(
      {
        positionId,
        payload,
      },
      {
        onSuccess: () => {
          setSavedPositionIds((current) => ({ ...current, [positionId]: true }));
          window.setTimeout(() => {
            setSavedPositionIds((current) => ({ ...current, [positionId]: false }));
          }, 1500);
        },
        onSettled: () => {
          setSavingPositionIds((current) => ({ ...current, [positionId]: false }));
        },
      },
    );
  };

  const handleQuantityBlur = (positionId: number, currentQuantity: number) => {
    submitQuantityUpdate(positionId, currentQuantity);
  };

  const submitQuantityUpdate = (positionId: number, currentQuantity: number) => {
    const draftValue = draftQuantities[positionId];
    if (draftValue === undefined) {
      return;
    }
    const parsed = Number(draftValue);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      resetQuantityDraft(positionId);
      return;
    }
    if (parsed === currentQuantity) {
      resetQuantityDraft(positionId);
      return;
    }
    submitPositionUpdate(positionId, { quantity: parsed });
    resetQuantityDraft(positionId);
  };

  const handleAverageCostBlur = (positionId: number, currentAverageCost: number) => {
    submitAverageCostUpdate(positionId, currentAverageCost);
  };

  const submitAverageCostUpdate = (positionId: number, currentAverageCost: number) => {
    const draftValue = draftAverageCosts[positionId];
    if (draftValue === undefined) {
      return;
    }
    const parsed = Number(draftValue);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      resetAverageCostDraft(positionId);
      return;
    }
    if (parsed === currentAverageCost) {
      resetAverageCostDraft(positionId);
      return;
    }
    submitPositionUpdate(positionId, { averageCost: parsed });
    resetAverageCostDraft(positionId);
  };

  const handleQuantityKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
    positionId: number,
    currentQuantity: number,
  ) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    submitQuantityUpdate(positionId, currentQuantity);
    event.currentTarget.blur();
  };

  const handleAverageCostKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
    positionId: number,
    currentAverageCost: number,
  ) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    submitAverageCostUpdate(positionId, currentAverageCost);
    event.currentTarget.blur();
  };

  const renderCurrencyValue = (value: number, currency: string) => {
    const displayCurrency = toDisplayCurrency(currency);
    if (displayCurrency == null) {
      return <span>{formatNumber(value)}</span>;
    }
    return <span>{`${displayCurrency} ${formatCurrency(value, displayCurrency)}`}</span>;
  };

  const analysis = analysisMutation.data;
  const concentrationRiskLevel =
    analysis?.computedSummary.concentrationRisk.level === "high"
      ? t("portfolio.ai.level.high")
      : analysis?.computedSummary.concentrationRisk.level === "moderate"
        ? t("portfolio.ai.level.moderate")
        : t("portfolio.ai.level.low");
  const analysisErrorMessage = analysisMutation.error instanceof Error ? analysisMutation.error.message : "";
  const showAiNotConfigured = analysisErrorMessage.includes(AI_NOT_CONFIGURED_MESSAGE);

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-hero-tile app-portfolio-hero">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("portfolio.title")}</h1>
            <p className="app-body-copy app-hero-description">{t("portfolio.description")}</p>
          </div>
          <p className="app-section-meta">
            {showPortfolioLoading ? t("portfolio.loading") : t("portfolio.count", { count: items.length })}
          </p>
        </Tile>
      </Column>

      {portfolioActionNoticeKind ? (
        <Column className="app-column-spacing" sm={4} md={8} lg={16}>
          <RequestStateNotice kind={portfolioActionNoticeKind} scopeLabel={t("portfolio.title")} />
        </Column>
      ) : null}

      {!showPortfolioLoading && !showPortfolioError && items.length > 0 ? (
        <Column className="app-column-spacing" sm={4} md={8} lg={16}>
          <Tile className="app-section-tile">
            <div className="app-section-header">
              <div>
                <p className="app-eyebrow">{t("portfolio.aggregate.eyebrow")}</p>
                <h2>{t("portfolio.equity.title")}</h2>
              </div>
            </div>
            <div className="app-assets-currency-columns">
              <div className="app-portfolio-aggregate-card">
                <p className="app-section-meta">USD</p>
                <p className="app-stock-card-price" data-testid="portfolio-total-usd">
                  {formatCurrency(totalUsd, "USD")}
                </p>
                <p className="app-section-meta">{t("portfolio.aggregate.combinedUsd")}</p>
                <p className="app-portfolio-combined-amount" data-testid="portfolio-combined-usd">
                  {formatCurrency(combinedUsd, "USD")}
                </p>
              </div>
              <div className="app-portfolio-aggregate-card">
                <p className="app-section-meta">TWD</p>
                <p className="app-stock-card-price" data-testid="portfolio-total-twd">
                  {formatCurrency(totalTwd, "TWD")}
                </p>
                <p className="app-section-meta">{t("portfolio.aggregate.combinedTwd")}</p>
                <p className="app-portfolio-combined-amount" data-testid="portfolio-combined-twd">
                  {formatCurrency(combinedTwd, "TWD")}
                </p>
              </div>
            </div>
            {portfolioAggregateNoticeKind ? (
              <div className="app-state-notice-wrap">
                <RequestStateNotice compact kind={portfolioAggregateNoticeKind} scopeLabel={t("portfolio.equity.title")} />
              </div>
            ) : null}
            <p className="app-section-meta">
              {t("portfolio.aggregate.fxRate")}:{" "}
              {usdTwdRate > 0 ? `1 USD = ${usdTwdRate.toFixed(4)} TWD` : "—"}
            </p>
            {summaryQuery.data && summaryQuery.data.skippedStockIds.length > 0 ? (
              <p className="app-section-meta">
                Quote unavailable: {summaryQuery.data.skippedStockIds.join(", ")}
              </p>
            ) : null}
          </Tile>
        </Column>
      ) : null}

      {!showPortfolioLoading && !showPortfolioError && items.length > 0 ? (
        <Column className="app-column-spacing" sm={4} md={8} lg={16}>
          <Tile className="app-section-tile">
            {holdingsHistoryNoticeKind ? (
              <div className="app-state-notice-wrap">
                <RequestStateNotice
                  compact
                  kind={holdingsHistoryNoticeKind}
                  scopeLabel={t("portfolio.equity.title")}
                />
              </div>
            ) : null}
            <div className="app-assets-currency-columns">
              <HistoricalValueChart
                title={t("portfolio.history.holdingsUsd")}
                series={holdingsHistoryQuery.data?.usd ?? null}
                range={historyRange}
                onZoomIn={() => setHistoryRange((current) => stepHistoryRange(current, "in"))}
                onZoomOut={() => setHistoryRange((current) => stepHistoryRange(current, "out"))}
                canZoomIn={canZoomIn}
                canZoomOut={canZoomOut}
                isLoading={holdingsHistoryQuery.isLoading}
              />
              <HistoricalValueChart
                title={t("portfolio.history.holdingsTwd")}
                series={holdingsHistoryQuery.data?.twd ?? null}
                range={historyRange}
                onZoomIn={() => setHistoryRange((current) => stepHistoryRange(current, "in"))}
                onZoomOut={() => setHistoryRange((current) => stepHistoryRange(current, "out"))}
                canZoomIn={canZoomIn}
                canZoomOut={canZoomOut}
                isLoading={holdingsHistoryQuery.isLoading}
              />
            </div>
          </Tile>
        </Column>
      ) : null}

      {!showPortfolioLoading && !showPortfolioError ? (
        <Column className="app-column-spacing" sm={4} md={8} lg={16}>
          <Tile className="app-section-tile">
            <div className="app-section-header">
              <div>
                <p className="app-eyebrow">{t("portfolio.ai.eyebrow")}</p>
                <h2>{t("portfolio.ai.title")}</h2>
                <p className="app-body-copy">{t("portfolio.ai.description")}</p>
              </div>
            </div>

            <div className="app-ai-insight-controls">
              <div className="app-stock-analysis-tags">
                {analysis?.provider ? <Tag type="cool-gray">{analysis.provider}</Tag> : null}
                {analysis?.model ? <Tag type="outline">{analysis.model}</Tag> : null}
              </div>
              <div className="app-portfolio-actions">
                <Link className="cds--link" href="/ai-settings">
                  {t("portfolio.ai.settings")}
                </Link>
                <Button
                  data-testid="portfolio-ai-analyze-button"
                  size="sm"
                  kind="secondary"
                  disabled={items.length === 0 || analysisMutation.isPending}
                  onClick={() => analysisMutation.mutate()}
                >
                  {analysis ? t("portfolio.ai.reanalyze") : t("portfolio.ai.analyze")}
                </Button>
              </div>
            </div>

            {items.length === 0 ? (
              <p className="app-supporting-copy">{t("portfolio.ai.empty")}</p>
            ) : null}

            {analysisMutation.isPending ? (
              <InlineLoading description={t("portfolio.ai.loading")} status="active" />
            ) : null}

            {showAiNotConfigured ? (
              <p data-testid="portfolio-ai-not-configured" className="app-supporting-copy">
                {t("portfolio.ai.notConfigured")}
              </p>
            ) : analysisMutation.error ? (
              <p className="app-supporting-copy app-negative">{t("portfolio.ai.failed")}</p>
            ) : null}

            {analysis?.warnings.length ? (
              <div className="app-stock-analysis-messages">
                {analysis.warnings.map((warning) => (
                  <p key={warning} className="app-supporting-copy">
                    {warning}
                  </p>
                ))}
              </div>
            ) : null}

            {analysis ? (
              <>
                <dl className="app-stock-analysis-grid">
                  <div className="app-stock-analysis-metric">
                    <dt>{t("portfolio.ai.totalMarketValueUsd")}</dt>
                    <dd>{formatCurrency(analysis.computedSummary.totalMarketValueUsd, "USD")}</dd>
                  </div>
                  <div className="app-stock-analysis-metric">
                    <dt>{t("portfolio.ai.totalMarketValueTwd")}</dt>
                    <dd>{formatCurrency(analysis.computedSummary.totalMarketValueTwd, "TWD")}</dd>
                  </div>
                  <div className="app-stock-analysis-metric">
                    <dt>{t("portfolio.ai.totalCostBasisUsd")}</dt>
                    <dd>{formatCurrency(analysis.computedSummary.totalCostBasisUsd, "USD")}</dd>
                  </div>
                  <div className="app-stock-analysis-metric">
                    <dt>{t("portfolio.ai.totalUnrealizedPlUsd")}</dt>
                    <dd>{formatSignedNumber(analysis.computedSummary.totalUnrealizedPlUsd, "USD")}</dd>
                  </div>
                  <div className="app-stock-analysis-metric">
                    <dt>{t("portfolio.ai.totalUnrealizedPlPercent")}</dt>
                    <dd>{formatPercent(analysis.computedSummary.totalUnrealizedPlPercent)}</dd>
                  </div>
                  <div className="app-stock-analysis-metric">
                    <dt>{t("portfolio.ai.topHolding")}</dt>
                    <dd>{formatPercent(analysis.computedSummary.concentrationRisk.topHoldingWeightPercent)}</dd>
                  </div>
                </dl>

                <div className="app-stock-analysis-body">
                  <h3>{t("portfolio.ai.concentration")}</h3>
                  <div className="app-assets-currency-columns">
                    <div className="app-portfolio-aggregate-card">
                      <p className="app-section-meta">{t("portfolio.ai.level")}</p>
                      <p className="app-portfolio-combined-amount">{concentrationRiskLevel}</p>
                    </div>
                    <div className="app-portfolio-aggregate-card">
                      <p className="app-section-meta">{t("portfolio.ai.top3")}</p>
                      <p className="app-portfolio-combined-amount">
                        {formatPercent(analysis.computedSummary.concentrationRisk.top3WeightPercent)}
                      </p>
                    </div>
                  </div>
                </div>

                {analysis.computedSummary.currencyExposure.length > 0 ? (
                  <div className="app-stock-analysis-body">
                    <h3>{t("portfolio.ai.exposure")}</h3>
                    <div className="app-assets-currency-columns">
                      {analysis.computedSummary.currencyExposure.map((exposure) => {
                        const nativeCurrency = toDisplayCurrency(exposure.currency);
                        return (
                          <div className="app-portfolio-aggregate-card" key={exposure.currency}>
                            <p className="app-section-meta">{exposure.currency}</p>
                            <p className="app-portfolio-combined-amount">
                              {nativeCurrency
                                ? formatCurrency(exposure.marketValue, nativeCurrency)
                                : formatNumber(exposure.marketValue)}
                            </p>
                            <p className="app-section-meta">{t("portfolio.ai.weight")}</p>
                            <p className="app-portfolio-secondary-amount">
                              {formatPercent(exposure.portfolioWeightPercent)}
                            </p>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}

                {analysis.computedSummary.largestHoldings.length > 0 ? (
                  <div className="app-stock-analysis-body">
                    <h3>{t("portfolio.ai.largestHoldings")}</h3>
                    <TableContainer className="app-portfolio-table-wrap">
                      <Table size="sm" useZebraStyles>
                        <TableHead>
                          <TableRow>
                            <TableHeader>{t("portfolio.ai.symbol")}</TableHeader>
                            <TableHeader>{t("portfolio.ai.weight")}</TableHeader>
                            <TableHeader>{t("portfolio.ai.marketValue")}</TableHeader>
                            <TableHeader>{t("portfolio.ai.unrealizedPl")}</TableHeader>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {analysis.computedSummary.largestHoldings.map((holding) => {
                            const nativeCurrency = toDisplayCurrency(holding.currency);
                            return (
                              <TableRow key={holding.stockId}>
                                <TableCell>
                                  <span className="app-result-symbol">{holding.symbol}</span>
                                </TableCell>
                                <TableCell>{formatPercent(holding.portfolioWeightPercent)}</TableCell>
                                <TableCell>
                                  {nativeCurrency
                                    ? renderCurrencyValue(holding.marketValue, nativeCurrency)
                                    : formatNumber(holding.marketValue)}
                                </TableCell>
                                <TableCell>
                                  {nativeCurrency
                                    ? formatSignedNumber(holding.unrealizedPl, nativeCurrency)
                                    : formatNumber(holding.unrealizedPl)}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  </div>
                ) : null}

                {analysis.computedSummary.skippedHoldings.length > 0 ? (
                  <div className="app-stock-analysis-body">
                    <h3>{t("portfolio.ai.skipped")}</h3>
                    <ul className="app-stock-analysis-links">
                      {analysis.computedSummary.skippedHoldings.map((holding) => (
                        <li key={holding.stockId}>
                          {holding.stockId}: {holding.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {analysis.aiReport ? (
                  <div className="app-stock-analysis-body">
                    <h3>{t("portfolio.ai.report")}</h3>
                    <p data-testid="portfolio-ai-report" className="app-supporting-copy app-stock-analysis-text">
                      {analysis.aiReport}
                    </p>
                  </div>
                ) : (
                  <p className="app-supporting-copy">{t("portfolio.ai.noReport")}</p>
                )}

                <p className="app-stock-analysis-disclaimer">{analysis.disclaimer}</p>
              </>
            ) : null}
          </Tile>
        </Column>
      ) : null}

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <StockSearch
          addLabel={t("portfolio.addHolding")}
          isSearching={searchQuery.isFetching}
          labels={{
            eyebrow: t("portfolio.search.eyebrow"),
            title: t("portfolio.search.title"),
            description: t("portfolio.search.description"),
            hint: t("portfolio.search.hint"),
            emptyPrompt: t("portfolio.search.emptyPrompt"),
          }}
          onQueryChange={setQuery}
          onToggle={(stockId) => {
            const positionId = positionIdByStockId.get(stockId);
            if (positionId) {
              portfolio.remove(positionId);
              return;
            }
            void portfolio.addByStockId(stockId);
          }}
          query={query}
          removeLabel={t("portfolio.removeHolding")}
          results={searchQuery.data ?? []}
          searchInputId="portfolio-stock-search"
          sectionTestId="portfolio-search-section"
          watchlistIds={portfolio.ids}
        />
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          {portfolioTableNoticeKind ? (
            <div className="app-state-notice-wrap">
              <RequestStateNotice kind={portfolioTableNoticeKind} scopeLabel={t("portfolio.title")} />
            </div>
          ) : null}
          {showPortfolioError ? (
            <RequestStateNotice kind={errorToNoticeKind(portfolio.error)} scopeLabel={t("portfolio.title")} />
          ) : showPortfolioLoading ? (
            <InlineLoading description={t("portfolio.loading")} status="active" />
          ) : isEmpty ? (
            <p className="app-supporting-copy">{t("portfolio.empty")}</p>
          ) : (
            <>
              <p className="app-section-meta" data-testid="portfolio-table-currency-hint">
                {t("portfolio.table.currencyHint")}
              </p>
              <TableContainer title={t("portfolio.title")} className="app-portfolio-table-wrap">
                <Table size="lg" useZebraStyles>
                  <TableHead>
                    <TableRow>
                      <TableHeader>{t("portfolio.column.symbol")}</TableHeader>
                      <TableHeader>{t("portfolio.column.name")}</TableHeader>
                      <TableHeader>{t("portfolio.column.market")}</TableHeader>
                      <TableHeader>{t("portfolio.column.quantity")}</TableHeader>
                      <TableHeader>{t("portfolio.column.averageCost")}</TableHeader>
                      <TableHeader>{t("portfolio.column.last")}</TableHeader>
                      <TableHeader>{t("portfolio.column.marketValue")}</TableHeader>
                      <TableHeader>{t("portfolio.column.change")}</TableHeader>
                      <TableHeader>{t("portfolio.column.changePercent")}</TableHeader>
                      <TableHeader>{t("portfolio.column.volume")}</TableHeader>
                      <TableHeader>{t("portfolio.column.updated")}</TableHeader>
                      <TableHeader>{t("portfolio.column.actions")}</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {items.map((position) => (
                      <TableRow data-testid={`portfolio-position-${position.stockId}`} key={position.id}>
                        <TableCell>
                          <span className="app-result-symbol">{position.stock.symbol}</span>
                        </TableCell>
                        <TableCell>
                          <span className="app-portfolio-name-cell">
                            {getDisplayStockName(position.stock, language)}
                          </span>
                        </TableCell>
                        <TableCell>{position.stock.exchange}</TableCell>
                        <TableCell>
                          <div className="app-portfolio-quantity-input-wrap">
                            <TextInput
                              data-testid={`portfolio-quantity-input-${position.stockId}`}
                              hideLabel
                              id={`portfolio-quantity-${position.id}`}
                              labelText={t("portfolio.column.quantity")}
                              onBlur={() => handleQuantityBlur(position.id, position.quantity)}
                              onChange={(event) =>
                                handleQuantityDraftChange(position.id, event.currentTarget.value)
                              }
                              onKeyDown={(event) =>
                                handleQuantityKeyDown(event, position.id, position.quantity)
                              }
                              size="sm"
                              type="number"
                              min={0}
                              step="any"
                              value={draftQuantities[position.id] ?? String(position.quantity)}
                              disabled={portfolio.isMutating}
                            />
                            <div className="app-portfolio-quantity-status">
                              {savingPositionIds[position.id] ? (
                                <span className="app-supporting-copy">{t("portfolio.quantity.saving")}</span>
                              ) : savedPositionIds[position.id] ? (
                                <span className="app-positive">{t("portfolio.quantity.saved")}</span>
                              ) : null}
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="app-portfolio-quantity-input-wrap">
                            <TextInput
                              data-testid={`portfolio-average-cost-input-${position.stockId}`}
                              hideLabel
                              id={`portfolio-average-cost-${position.id}`}
                              labelText={t("portfolio.column.averageCost")}
                              onBlur={() => handleAverageCostBlur(position.id, position.averageCost)}
                              onChange={(event) =>
                                handleAverageCostDraftChange(position.id, event.currentTarget.value)
                              }
                              onKeyDown={(event) =>
                                handleAverageCostKeyDown(event, position.id, position.averageCost)
                              }
                              size="sm"
                              type="number"
                              min={0}
                              step="any"
                              value={draftAverageCosts[position.id] ?? String(position.averageCost)}
                              disabled={portfolio.isMutating}
                            />
                          </div>
                        </TableCell>
                        <TableCell>
                          {position.quote != null
                            ? renderCurrencyValue(position.quote.lastPrice, position.stock.currency)
                            : "—"}
                        </TableCell>
                        <TableCell>{renderCurrencyValue(position.marketValue, position.stock.currency)}</TableCell>
                        <TableCell>
                          {position.quote != null ? (
                            <span className={position.quote.change >= 0 ? "app-positive" : "app-negative"}>
                              {formatSignedNumber(position.quote.change, position.stock.currency)}
                            </span>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell>
                          {position.quote != null ? (
                            <span className={position.quote.changePercent >= 0 ? "app-positive" : "app-negative"}>
                              {formatPercent(position.quote.changePercent)}
                            </span>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell>
                          {position.quote?.volume != null ? formatNumber(position.quote.volume) : "—"}
                        </TableCell>
                        <TableCell>
                          {position.quote?.timestamp
                            ? formatLastUpdated(position.quote.timestamp)
                            : "—"}
                        </TableCell>
                        <TableCell>
                          <div className="app-portfolio-actions">
                            <Link
                              className="cds--link"
                              href={`/stock/${encodeURIComponent(position.stock.id)}`}
                            >
                              {t("stock.viewDetails")}
                            </Link>
                            <Button
                              data-testid={`portfolio-remove-${position.stockId}`}
                              kind="ghost"
                              size="sm"
                              onClick={() => portfolio.remove(position.id)}
                            >
                              {t("portfolio.removeHolding")}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </>
          )}
        </Tile>
      </Column>
    </Grid>
  );
}
