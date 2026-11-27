import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TWStockInsights } from "@shared/api";

import { LanguageProvider } from "../../app/i18n";
import { TwInsightsPanel } from "./TwInsightsPanel";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

const basePeer = {
  sector: "Semiconductors",
  asOf: "2026-11-27",
  companyCount: 10,
  metrics: [],
  compareUrl: null,
  topMovers: [],
};

describe("TwInsightsPanel", () => {
  it("shows honest unavailable revenue and dividends without synthetic values", () => {
    const insights: TWStockInsights = {
      revenue: {
        status: "unavailable",
        reason: "official_monthly_revenue_not_collected",
        source: null,
        reportingPeriod: null,
        reportGeneratedDate: null,
        retrievedAt: null,
        lastUpdated: null,
        latestRevenueK: null,
        latestMomPercent: null,
        latestYoyPercent: null,
        points: [],
      },
      peer: basePeer,
      dividends: {
        status: "unavailable",
        reason: "official_dividend_history_not_collected",
        source: null,
        avgCashYield2Y: null,
        years: [],
      },
    };

    renderWithLanguage(<TwInsightsPanel isLoading={false} insights={insights} />);
    expect(screen.getByTestId("tw-insights-revenue-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("tw-insights-dividends-unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/1,000/)).not.toBeInTheDocument();
  });

  it("renders a single official month without inventing a multi-month chart", () => {
    const insights: TWStockInsights = {
      revenue: {
        status: "ok",
        reason: null,
        source: "official_monthly_revenue:TWSE",
        reportingPeriod: "2026-10",
        reportGeneratedDate: "2026-11-17",
        retrievedAt: "2026-11-27T10:14:11Z",
        lastUpdated: "2026-11-27T10:14:11Z",
        latestRevenueK: 586890319,
        latestMomPercent: 14.66,
        latestYoyPercent: 59.71,
        points: [
          {
            month: "2026/10",
            revenueK: 586890319,
            momPercent: 14.66,
            yoyPercent: 59.71,
          },
        ],
      },
      peer: basePeer,
      dividends: {
        status: "unavailable",
        reason: "official_dividend_history_not_collected",
        source: null,
        avgCashYield2Y: null,
        years: [],
      },
    };

    renderWithLanguage(<TwInsightsPanel isLoading={false} insights={insights} />);
    expect(screen.getByTestId("tw-insights-revenue-single-month")).toBeInTheDocument();
    expect(screen.getByText(/586,890,319/)).toBeInTheDocument();
  });
});
