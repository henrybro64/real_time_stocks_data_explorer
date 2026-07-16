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

  it("does not flash unavailable cards during the initial loading state", () => {
    renderWithLanguage(<MarketIndicesStrip isLoading items={[]} />);

    expect(screen.getByText("載入市場指數中")).toBeInTheDocument();
    expect(screen.queryByTestId("market-index-card-TWSE:TAIEX")).not.toBeInTheDocument();
    expect(screen.queryByText("資料來源暫時不可用")).not.toBeInTheDocument();
    expect(screen.queryByText("暫時無法取得")).not.toBeInTheDocument();
  });

  it("applies market-specific price tone classes to index changes", () => {
    renderWithLanguage(
      <MarketIndicesStrip
        items={[
          okIndex({
            id: "TWSE:TAIEX",
            market: "TW",
            label: "Taiwan Stock Exchange",
            change: 85.44,
            changePercent: 0.39,
          }),
          okIndex({ id: "^DJI", market: "US", label: "DOW Jones", change: -102.12, changePercent: -0.26 }),
          okIndex({ id: "^NDQ", market: "US", label: "Nasdaq" }),
          okIndex({ id: "^SPX", market: "US", label: "S&P 500" }),
        ]}
      />,
    );

    expect(screen.getByTestId("market-index-change-TWSE:TAIEX")).toHaveClass(
      "app-price-up",
      "app-price-convention-taiwan",
    );
    expect(screen.getByTestId("market-index-change-^DJI")).toHaveClass(
      "app-price-down",
      "app-price-convention-us",
    );
    expect(screen.getByTestId("market-index-change-TWSE:TAIEX")).toHaveTextContent("+");
    expect(screen.getByTestId("market-index-change-^DJI")).toHaveTextContent("-");
  });
});
