"use client";

import {
  Column,
  Grid,
  InlineLoading,
  Button,
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
import { useDeferredValue, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Stock } from "@shared/stock";
import Link from "next/link";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { StockSearch } from "../../components/search/StockSearch";
import { usePortfolioPositions } from "../../hooks/usePortfolioPositions";
import { fetchPortfolioSummary, fetchUsdTwdFxRate, searchStocks } from "../../services/api/client";
import { formatCurrency, formatLastUpdated, formatPercent, formatSignedNumber } from "../../services/format/quote";

export function PortfolioPage() {
  const { t, language, formatNumber } = useLanguage();
  const [query, setQuery] = useState("");
  const [draftQuantities, setDraftQuantities] = useState<Record<number, string>>({});
  const [savingPositionIds, setSavingPositionIds] = useState<Record<number, boolean>>({});
  const [savedPositionIds, setSavedPositionIds] = useState<Record<number, boolean>>({});
  const deferredQuery = useDeferredValue(query);
  const portfolio = usePortfolioPositions();

  const searchQuery = useQuery<Stock[]>({
    queryKey: ["portfolio-search", deferredQuery],
    queryFn: () => searchStocks(deferredQuery),
    enabled: deferredQuery.trim().length > 0,
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
  /** While the first fetch runs, `data` is undefined so `items` is [] — do not treat as empty. */
  const showPortfolioLoading = portfolio.isLoading && items.length === 0;
  const showPortfolioError = portfolio.isError && items.length === 0;
  const isEmpty = !portfolio.isLoading && !portfolio.isError && items.length === 0;

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

  const handleQuantityDraftChange = (positionId: number, nextValue: string) => {
    setDraftQuantities((current) => ({ ...current, [positionId]: nextValue }));
  };

  const resetQuantityDraft = (positionId: number) => {
    setDraftQuantities((current) => {
      const next = { ...current };
      delete next[positionId];
      return next;
    });
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
    setSavingPositionIds((current) => ({ ...current, [positionId]: true }));
    setSavedPositionIds((current) => ({ ...current, [positionId]: false }));
    portfolio.update(
      {
        positionId,
        payload: { quantity: parsed },
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
    resetQuantityDraft(positionId);
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
          {showPortfolioError ? (
            <p className="app-supporting-copy app-negative">{t("portfolio.loadError")}</p>
          ) : showPortfolioLoading ? (
            <InlineLoading description={t("portfolio.loading")} status="active" />
          ) : isEmpty ? (
            <p className="app-supporting-copy">{t("portfolio.empty")}</p>
          ) : (
            <TableContainer title={t("portfolio.title")} className="app-portfolio-table-wrap">
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("portfolio.column.symbol")}</TableHeader>
                    <TableHeader>{t("portfolio.column.name")}</TableHeader>
                    <TableHeader>{t("portfolio.column.market")}</TableHeader>
                    <TableHeader>{t("portfolio.column.quantity")}</TableHeader>
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
                      <TableCell>{formatCurrency(position.quote.lastPrice, position.stock.currency)}</TableCell>
                      <TableCell>{formatCurrency(position.marketValue, position.stock.currency)}</TableCell>
                      <TableCell>
                        <span className={position.quote.change >= 0 ? "app-positive" : "app-negative"}>
                          {formatSignedNumber(position.quote.change, position.stock.currency)}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className={position.quote.changePercent >= 0 ? "app-positive" : "app-negative"}>
                          {formatPercent(position.quote.changePercent)}
                        </span>
                      </TableCell>
                      <TableCell>
                        {position.quote.volume != null ? formatNumber(position.quote.volume) : "—"}
                      </TableCell>
                      <TableCell>{formatLastUpdated(position.quote.timestamp)}</TableCell>
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
          )}
        </Tile>
      </Column>
    </Grid>
  );
}
