"use client";

import {
  Button,
  Column,
  Grid,
  InlineLoading,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tile,
} from "@carbon/react";
import { useDeferredValue, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { QuoteResponse } from "@shared/api";
import type { Stock } from "@shared/stock";
import Link from "next/link";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { StockSearch } from "../../components/search/StockSearch";
import { useLocalPortfolio } from "../../hooks/useLocalStockList";
import { fetchQuotes, searchStocks } from "../../services/api/client";
import { formatCurrency, formatLastUpdated, formatPercent, formatSignedNumber } from "../../services/format/quote";

export function PortfolioPage() {
  const { t, language, formatNumber } = useLanguage();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const portfolio = useLocalPortfolio();

  const portfolioQuery = useQuery<QuoteResponse[]>({
    queryKey: ["portfolio-quotes", portfolio.ids],
    queryFn: () => fetchQuotes(portfolio.ids),
    enabled: portfolio.ids.length > 0,
    refetchInterval: 5_000,
  });

  const searchQuery = useQuery<Stock[]>({
    queryKey: ["portfolio-search", deferredQuery],
    queryFn: () => searchStocks(deferredQuery),
    enabled: deferredQuery.trim().length > 0,
  });

  const items = portfolioQuery.data ?? [];
  const isEmpty = portfolio.ids.length === 0;

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-hero-tile app-portfolio-hero">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("portfolio.title")}</h1>
            <p className="app-body-copy app-hero-description">{t("portfolio.description")}</p>
          </div>
          <p className="app-section-meta">{t("portfolio.count", { count: portfolio.ids.length })}</p>
        </Tile>
      </Column>

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
          onToggle={portfolio.toggle}
          query={query}
          removeLabel={t("portfolio.removeHolding")}
          results={searchQuery.data ?? []}
          searchInputId="portfolio-stock-search"
          watchlistIds={portfolio.ids}
        />
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          {isEmpty ? (
            <p className="app-supporting-copy">{t("portfolio.empty")}</p>
          ) : portfolioQuery.isLoading ? (
            <InlineLoading description={t("portfolio.loading")} status="active" />
          ) : (
            <TableContainer title={t("portfolio.title")} className="app-portfolio-table-wrap">
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("portfolio.column.symbol")}</TableHeader>
                    <TableHeader>{t("portfolio.column.name")}</TableHeader>
                    <TableHeader>{t("portfolio.column.market")}</TableHeader>
                    <TableHeader>{t("portfolio.column.last")}</TableHeader>
                    <TableHeader>{t("portfolio.column.change")}</TableHeader>
                    <TableHeader>{t("portfolio.column.changePercent")}</TableHeader>
                    <TableHeader>{t("portfolio.column.volume")}</TableHeader>
                    <TableHeader>{t("portfolio.column.updated")}</TableHeader>
                    <TableHeader>{t("portfolio.column.actions")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {items.map(({ stock, quote }) => (
                    <TableRow key={stock.id}>
                      <TableCell>
                        <span className="app-result-symbol">{stock.symbol}</span>
                      </TableCell>
                      <TableCell>{getDisplayStockName(stock, language)}</TableCell>
                      <TableCell>{stock.exchange}</TableCell>
                      <TableCell>{formatCurrency(quote.lastPrice, stock.currency)}</TableCell>
                      <TableCell>
                        <span className={quote.change >= 0 ? "app-positive" : "app-negative"}>
                          {formatSignedNumber(quote.change, stock.currency)}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className={quote.changePercent >= 0 ? "app-positive" : "app-negative"}>
                          {formatPercent(quote.changePercent)}
                        </span>
                      </TableCell>
                      <TableCell>
                        {quote.volume != null ? formatNumber(quote.volume) : "—"}
                      </TableCell>
                      <TableCell>{formatLastUpdated(quote.timestamp)}</TableCell>
                      <TableCell>
                        <div className="app-portfolio-actions">
                          <Link
                            className="cds--link"
                            href={`/stock/${encodeURIComponent(stock.id)}`}
                          >
                            {t("stock.viewDetails")}
                          </Link>
                          <Button kind="ghost" size="sm" onClick={() => portfolio.remove(stock.id)}>
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
