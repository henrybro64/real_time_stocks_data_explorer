import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { KeyStatistics } from "@shared/api";

import { LanguageProvider } from "../../app/i18n";
import { KeyStatisticsPanel } from "./KeyStatisticsPanel";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

const sampleStats: KeyStatistics = {
  open: 950,
  previousClose: 945,
  volume: 123_456,
  lastPrice: 950,
  dayLow: 948,
  dayHigh: 960,
  fiftyTwoWeekLow: 720,
  fiftyTwoWeekHigh: 1100,
  fiftyTwoWeekLowDate: "2025-04-14",
  fiftyTwoWeekHighDate: "2025-10-15",
  avgVolume10d: 120_000,
  marketCap: "24.1T",
  sharesOutstanding: "25.9B",
  beta: 1.1,
  peRatio: 20,
  peRatioType: "official_daily",
  peRatioSource: "TWSE official daily P/E",
  priceUsed: 950,
  priceAsOf: "2026-07-03",
  peTtm: 20,
  epsTtm: 40,
  epsAsOf: null,
  valuationAsOf: "2026-07-03",
  valuationWarning: null,
  dividendQuarterly: 4.5,
  dividendYieldPercent: 1.8,
  exDividendDate: "2026-06-20",
  dividendPayDate: "2026-07-18",
  nextEarningsDate: "2026-07-24",
  dayRangeMarkerPercent: 50,
  week52RangeMarkerPercent: 60,
};

describe("KeyStatisticsPanel", () => {
  it("renders trailing P/E from key statistics payload", () => {
    renderWithLanguage(<KeyStatisticsPanel stats={sampleStats} currency="TWD" />);

    expect(screen.getByTestId("key-statistics-section")).toBeInTheDocument();
    expect(screen.getByText("20.00x")).toBeInTheDocument();
  });
});
