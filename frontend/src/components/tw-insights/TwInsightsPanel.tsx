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

function formatSignedPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatRevenueK(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US").format(value);
}

function formatDividend(value: number): string {
  return value === 0 ? "-" : value.toFixed(4);
}

export function TwInsightsPanel({ isLoading, insights }: Props) {
  const { t } = useLanguage();

  if (isLoading) {
    return (
      <Tile className="app-section-tile" data-testid="tw-insights-section">
        <InlineLoading description={t("twInsights.loading")} status="active" />
      </Tile>
    );
  }

  if (!insights) {
    return (
      <Tile className="app-section-tile" data-testid="tw-insights-section">
        <p className="app-supporting-copy">{t("twInsights.unavailable")}</p>
      </Tile>
    );
  }

  const revenueOk = insights.revenue.status === "ok";
  const revenuePoints = revenueOk ? insights.revenue.points.slice(-12) : [];
  const maxRevenue = Math.max(
    ...revenuePoints.map((item) => item.revenueK ?? 0),
    1,
  );
  const dividendsOk = insights.dividends.status === "ok";

  return (
    <Tile className="app-section-tile app-tw-insights" data-testid="tw-insights-section">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Taiwan insights</p>
          <h2>{t("twInsights.title")}</h2>
        </div>
        <p className="app-section-meta">{t("twInsights.description")}</p>
      </div>

      <section className="app-tw-block">
        <h3 className="app-tw-block-title">{t("twInsights.revenue.title")}</h3>
        {revenueOk ? (
          <>
            <p className="app-section-meta">
              {t("twInsights.revenue.latest")}: {formatRevenueK(insights.revenue.latestRevenueK)} K /{" "}
              {formatSignedPercent(insights.revenue.latestMomPercent)} MoM /{" "}
              {formatSignedPercent(insights.revenue.latestYoyPercent)} YoY
              {insights.revenue.reportingPeriod
                ? ` · ${insights.revenue.reportingPeriod}`
                : null}
            </p>
            {revenuePoints.length > 1 ? (
              <div className="app-tw-revenue-bars">
                {revenuePoints.map((item) => {
                  const height = Math.max(
                    8,
                    Math.round(((item.revenueK ?? 0) / maxRevenue) * 120),
                  );
                  return (
                    <div className="app-tw-bar-item" key={item.month}>
                      <div
                        className="app-tw-bar"
                        style={{ height: `${height}px` }}
                        title={`${item.month}: ${item.revenueK ?? "—"}`}
                      />
                      <span className="app-tw-bar-label">{item.month.slice(5)}</span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="app-supporting-copy" data-testid="tw-insights-revenue-single-month">
                {t("twInsights.revenue.singleMonthNote")}
              </p>
            )}
          </>
        ) : (
          <p className="app-supporting-copy" data-testid="tw-insights-revenue-unavailable">
            {t("twInsights.revenue.unavailable")}
          </p>
        )}
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
      </section>

      <section className="app-tw-block">
        <h3 className="app-tw-block-title">{t("twInsights.dividend.title")}</h3>
        {dividendsOk ? (
          <>
            <p className="app-section-meta">
              {t("twInsights.dividend.avg2y")}: {(insights.dividends.avgCashYield2Y ?? 0).toFixed(2)}%
            </p>
            <Tabs>
              <TabList aria-label="Dividend views">
                <Tab>{t("twInsights.dividend.distributionTitle")}</Tab>
                <Tab>{t("twInsights.dividend.policyTitle")}</Tab>
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
                          <TableCell>{row.exDividendDate ?? "—"}</TableCell>
                          <TableCell>{row.payDate ?? "—"}</TableCell>
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
                        <TableHeader>{t("twInsights.dividend.byEarningsYear")}</TableHeader>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {insights.dividends.years.map((row) => (
                        <TableRow key={`earn-${row.year}`}>
                          <TableCell>{row.year}</TableCell>
                          <TableCell>
                            {formatDividend(row.cashDividend)} / {formatDividend(row.stockDividend)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TabPanel>
              </TabPanels>
            </Tabs>
          </>
        ) : (
          <p className="app-supporting-copy" data-testid="tw-insights-dividends-unavailable">
            {t("twInsights.dividend.unavailable")}
          </p>
        )}
      </section>
    </Tile>
  );
}
