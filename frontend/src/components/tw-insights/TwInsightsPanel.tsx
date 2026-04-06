"use client";

import {
  InlineLoading,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  Tile,
} from "@carbon/react";
import type { TWStockInsights } from "@shared/api";
import { useLanguage } from "../../app/i18n";

type Props = {
  isLoading: boolean;
  insights: TWStockInsights | null;
};

function formatSignedPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatRevenueK(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatDividend(value: number): string {
  return value === 0 ? "-" : value.toFixed(4);
}

export function TwInsightsPanel({ isLoading, insights }: Props) {
  const { t } = useLanguage();

  if (isLoading) {
    return (
      <Tile className="app-section-tile">
        <InlineLoading description={t("twInsights.loading")} status="active" />
      </Tile>
    );
  }

  if (!insights) {
    return null;
  }

  const revenuePoints = insights.revenue.points.slice(-12);
  const maxRevenue = Math.max(...revenuePoints.map((item) => item.revenueK), 1);

  return (
    <Tile className="app-section-tile app-tw-insights">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Taiwan insights</p>
          <h2>{t("twInsights.title")}</h2>
        </div>
        <p className="app-section-meta">{t("twInsights.description")}</p>
      </div>

      <section className="app-tw-block">
        <h3 className="app-tw-block-title">{t("twInsights.revenue.title")}</h3>
        <p className="app-section-meta">
          {t("twInsights.revenue.latest")}: {formatRevenueK(insights.revenue.latestRevenueK)} K /{" "}
          {formatSignedPercent(insights.revenue.latestMomPercent)} MoM /{" "}
          {formatSignedPercent(insights.revenue.latestYoyPercent)} YoY
        </p>
        <div className="app-tw-revenue-bars">
          {revenuePoints.map((item) => {
            const height = Math.max(8, Math.round((item.revenueK / maxRevenue) * 120));
            return (
              <div className="app-tw-bar-item" key={item.month}>
                <div className="app-tw-bar" style={{ height: `${height}px` }} title={`${item.month}: ${item.revenueK}`} />
                <span className="app-tw-bar-label">{item.month.slice(5)}</span>
              </div>
            );
          })}
        </div>
      </section>

      <section className="app-tw-block">
        <h3 className="app-tw-block-title">{t("twInsights.peer.title")}</h3>
        {insights.peer.compareUrl ? (
          <p className="app-section-meta">
            Source:{" "}
            <a href={insights.peer.compareUrl} rel="noreferrer" target="_blank">
              Yahoo Finance compare
            </a>
          </p>
        ) : null}
        <Table size="sm" useZebraStyles>
          <TableHead>
            <TableRow>
              <TableHeader>{t("twInsights.peer.period")}</TableHeader>
              <TableHeader>{t("twInsights.peer.company")}</TableHeader>
              <TableHeader>{t("twInsights.peer.industry")}</TableHeader>
              <TableHeader>{t("twInsights.peer.rank")}</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {insights.peer.metrics.map((metric) => (
              <TableRow key={metric.period}>
                <TableCell>{metric.label}</TableCell>
                <TableCell>{formatSignedPercent(metric.companyPercent)}</TableCell>
                <TableCell>{formatSignedPercent(metric.industryAveragePercent)}</TableCell>
                <TableCell>
                  {metric.rank}/{metric.total}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {insights.peer.topMovers.length > 0 ? (
          <>
            <h4 className="app-tw-subtitle">Industry movers (1M/3M/6M/1Y)</h4>
            <Table size="sm" useZebraStyles>
              <TableHead>
                <TableRow>
                  <TableHeader>#</TableHeader>
                  <TableHeader>Company</TableHeader>
                  <TableHeader>Symbol</TableHeader>
                  <TableHeader>1M</TableHeader>
                  <TableHeader>3M</TableHeader>
                  <TableHeader>6M</TableHeader>
                  <TableHeader>1Y</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {insights.peer.topMovers.map((item) => (
                  <TableRow key={`${item.rank}-${item.symbol}`}>
                    <TableCell>{item.rank}</TableCell>
                    <TableCell>{item.name}</TableCell>
                    <TableCell>{item.symbol}</TableCell>
                    <TableCell>{formatSignedPercent(item.change1mPercent)}</TableCell>
                    <TableCell>{formatSignedPercent(item.change3mPercent)}</TableCell>
                    <TableCell>{formatSignedPercent(item.change6mPercent)}</TableCell>
                    <TableCell>{formatSignedPercent(item.change1yPercent)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        ) : null}
      </section>

      <section className="app-tw-block">
        <h3 className="app-tw-block-title">{t("twInsights.dividend.title")}</h3>
        <h4 className="app-tw-subtitle">{t("twInsights.dividend.distributionTitle")}</h4>
        <div className="app-tw-dividend-bars">
          {insights.dividends.years
            .slice()
            .reverse()
            .map((row) => (
              <div className="app-tw-dividend-bar-item" key={row.year}>
                <div className="app-tw-dividend-bar-stack">
                  <div className="app-tw-dividend-bar app-tw-dividend-bar-stock" style={{ height: `${row.stockDividend * 34}px` }} />
                  <div className="app-tw-dividend-bar app-tw-dividend-bar-cash" style={{ height: `${row.cashDividend * 34}px` }} />
                </div>
                <span className="app-tw-bar-label">{row.year}</span>
              </div>
            ))}
        </div>

        <p className="app-section-meta">
          {t("twInsights.dividend.avg2y")}: {insights.dividends.avgCashYield2Y.toFixed(2)}%
        </p>
        <h4 className="app-tw-subtitle">{t("twInsights.dividend.policyTitle")}</h4>
        <Tabs>
          <TabList contained aria-label={t("twInsights.dividend.policyTitle")}>
            <Tab>{t("twInsights.dividend.byPayoutYear")}</Tab>
            <Tab>{t("twInsights.dividend.byEarningsYear")}</Tab>
          </TabList>
          <TabPanels>
            <TabPanel>
              <Table size="sm" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("twInsights.dividend.year")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.cash")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.stock")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.yield")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.exDate")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.payDate")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {insights.dividends.years.map((row) => (
                    <TableRow key={row.year}>
                      <TableCell>{row.year}</TableCell>
                      <TableCell>{formatDividend(row.cashDividend)}</TableCell>
                      <TableCell>{formatDividend(row.stockDividend)}</TableCell>
                      <TableCell>{row.cashYieldPercent.toFixed(2)}%</TableCell>
                      <TableCell>{row.exDividendDate ?? "-"}</TableCell>
                      <TableCell>{row.payDate ?? "-"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TabPanel>
            <TabPanel>
              <Table size="sm" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("twInsights.dividend.earningsYear")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.cash")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.stock")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.yield")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.exDate")}</TableHeader>
                    <TableHeader>{t("twInsights.dividend.payDate")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {insights.dividends.years.map((row) => (
                    <TableRow key={`earnings-${row.year}`}>
                      <TableCell>{row.year}</TableCell>
                      <TableCell>{formatDividend(row.cashDividend)}</TableCell>
                      <TableCell>{formatDividend(row.stockDividend)}</TableCell>
                      <TableCell>{row.cashYieldPercent.toFixed(2)}%</TableCell>
                      <TableCell>{row.exDividendDate ?? "-"}</TableCell>
                      <TableCell>{row.payDate ?? "-"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TabPanel>
          </TabPanels>
        </Tabs>
      </section>
    </Tile>
  );
}
