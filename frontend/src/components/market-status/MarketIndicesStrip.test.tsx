import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MarketIndex } from "@shared/api";
import { LanguageProvider } from "../../app/i18n";
import { MarketIndicesStrip, resolveMarketIndexSlots } from "./MarketIndicesStrip";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

function okIndex(partial: Partial<MarketIndex> & Pick<MarketIndex, "id" | "label" | "market">): MarketIndex {
  return {
    value: 100,
    change: 1,
    changePercent: 1,
    timestamp: "2026-07-13T01:00:00Z",
    isMarketOpen: false,
    status: "ok",
    unavailableReason: null,
    ...partial,
  };
}

describe("resolveMarketIndexSlots", () => {
  it("fills missing Taiwan slot so the layout stays at four cards", () => {
    const resolved = resolveMarketIndexSlots([
      okIndex({ id: "^DJI", market: "US", label: "DOW Jones" }),
      okIndex({ id: "^NDQ", market: "US", label: "Nasdaq" }),
      okIndex({ id: "^SPX", market: "US", label: "S&P 500" }),
    ]);

    expect(resolved).toHaveLength(4);
    expect(resolved[0]).toMatchObject({
      id: "TWSE:TAIEX",
      status: "unavailable",
      value: null,
    });
  });
});

describe("MarketIndicesStrip", () => {
  it("keeps a consistent four-card layout when Taiwan is unavailable", () => {
    renderWithLanguage(
      <MarketIndicesStrip
        items={[
          {
            id: "TWSE:TAIEX",
            market: "TW",
            label: "Taiwan Stock Exchange",
            value: null,
            change: null,
            changePercent: null,
            timestamp: "2026-07-13T01:00:00Z",
            isMarketOpen: false,
            status: "unavailable",
            unavailableReason: "taiwan_provider_unavailable",
          },
          okIndex({ id: "^DJI", market: "US", label: "DOW Jones", value: 39000 }),
          okIndex({ id: "^NDQ", market: "US", label: "Nasdaq", value: 17000 }),
          okIndex({ id: "^SPX", market: "US", label: "S&P 500", value: 5300 }),
        ]}
      />,
    );

    expect(screen.getByTestId("market-index-card-TWSE:TAIEX")).toBeInTheDocument();
    expect(screen.getByTestId("market-index-card-^DJI")).toBeInTheDocument();
    expect(screen.getByTestId("market-index-card-^NDQ")).toBeInTheDocument();
    expect(screen.getByTestId("market-index-card-^SPX")).toBeInTheDocument();
    expect(screen.getAllByText("暫時無法取得").length).toBeGreaterThan(0);
    expect(screen.getByText("Taiwan Stock Exchange")).toBeInTheDocument();
    expect(screen.getByText("DOW Jones")).toBeInTheDocument();
  });

  it("still renders four cards when the API silently omits Taiwan", () => {
    renderWithLanguage(
      <MarketIndicesStrip
        items={[
          okIndex({ id: "^DJI", market: "US", label: "DOW Jones" }),
          okIndex({ id: "^NDQ", market: "US", label: "Nasdaq" }),
          okIndex({ id: "^SPX", market: "US", label: "S&P 500" }),
        ]}
      />,
    );

    expect(screen.getByTestId("market-index-card-TWSE:TAIEX")).toBeInTheDocument();
    expect(screen.getByTestId("market-index-card-^DJI")).toBeInTheDocument();
    // Partial failure: one card notice only; no section-level outage banner.
    expect(screen.getAllByText("資料來源暫時不可用")).toHaveLength(1);
    expect(screen.getByText("Taiwan Stock Exchange")).toBeInTheDocument();
    expect(screen.getByText("DOW Jones")).toBeInTheDocument();
  });
});
