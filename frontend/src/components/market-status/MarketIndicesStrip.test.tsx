import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MarketIndex } from "@shared/api";
import { MARKET_INDEX_CATALOG } from "@shared/marketIndexCatalog";
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

function catalogOkIndices(
  overrides: Partial<Record<string, Partial<MarketIndex>>> = {},
): MarketIndex[] {
  return MARKET_INDEX_CATALOG.map((slot) =>
    okIndex({
      id: slot.id,
      market: slot.market,
      label: slot.label,
      ...overrides[slot.id],
    }),
  );
}

const CATALOG_IDS = MARKET_INDEX_CATALOG.map((slot) => slot.id);
const ZH_SUBTITLE = MARKET_INDEX_CATALOG.map((slot) => slot.labelZhTw).join(" / ");

describe("resolveMarketIndexSlots", () => {
  it("fills every omitted catalog slot so the layout stays complete", () => {
    const kept = okIndex({ id: "^DJI", market: "US", label: "DOW Jones" });
    const resolved = resolveMarketIndexSlots([kept]);

    expect(resolved.map((item) => item.id)).toEqual(CATALOG_IDS);
    for (const item of resolved) {
      if (item.id === kept.id) {
        expect(item.status).toBe("ok");
      } else {
        expect(item).toMatchObject({ status: "unavailable", value: null });
      }
    }
  });
});

describe("MarketIndicesStrip", () => {
  it("keeps a consistent catalog layout when Taiwan is unavailable", () => {
    const tw = MARKET_INDEX_CATALOG[0];
    renderWithLanguage(
      <MarketIndicesStrip
        items={catalogOkIndices({
          [tw.id]: {
            value: null,
            change: null,
            changePercent: null,
            status: "unavailable",
            unavailableReason: "taiwan_provider_unavailable",
          },
          "^DJI": { value: 39000 },
          "^NDQ": { value: 17000 },
          "^SPX": { value: 5300 },
          "^SOX": { value: 5488.12 },
        })}
      />,
    );

    for (const id of CATALOG_IDS) {
      expect(screen.getByTestId(`market-index-card-${id}`)).toBeInTheDocument();
    }
    expect(screen.getAllByText("暫時無法取得").length).toBeGreaterThan(0);
    expect(screen.getByText(tw.label)).toBeInTheDocument();
    expect(screen.getByText("DOW Jones")).toBeInTheDocument();
    expect(screen.getByText("PHLX Semiconductor")).toBeInTheDocument();
    expect(screen.getByText(ZH_SUBTITLE)).toBeInTheDocument();
  });

  it("still renders catalog cards when the API silently omits Taiwan", () => {
    renderWithLanguage(
      <MarketIndicesStrip items={catalogOkIndices().filter((item) => item.market !== "TW")} />,
    );

    expect(screen.getByTestId("market-index-card-TWSE:TAIEX")).toBeInTheDocument();
    expect(screen.getByTestId("market-index-card-^DJI")).toBeInTheDocument();
    expect(screen.getByTestId("market-index-card-^SOX")).toBeInTheDocument();
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
        items={catalogOkIndices({
          "TWSE:TAIEX": { change: 85.44, changePercent: 0.39 },
          "^DJI": { change: -102.12, changePercent: -0.26 },
          "^SOX": { change: 21.4, changePercent: 0.39 },
        })}
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
    expect(screen.getByTestId("market-index-change-^SOX")).toHaveClass(
      "app-price-up",
      "app-price-convention-us",
    );
    expect(screen.getByTestId("market-index-change-^SOX")).toHaveTextContent("+");
  });

  it("keeps other index cards when SOX is unavailable", () => {
    renderWithLanguage(
      <MarketIndicesStrip
        items={catalogOkIndices({
          "^DJI": { value: 39000 },
          "^NDQ": { value: 17000 },
          "^SPX": { value: 5300 },
          "^SOX": {
            value: null,
            change: null,
            changePercent: null,
            status: "unavailable",
            unavailableReason: "us_provider_unavailable",
          },
        })}
      />,
    );

    expect(screen.getByTestId("market-index-card-^SOX")).toBeInTheDocument();
    expect(screen.getByTestId("market-index-card-^DJI")).toBeInTheDocument();
    expect(screen.getByText("39,000.00")).toBeInTheDocument();
    expect(screen.getAllByText("資料來源暫時不可用")).toHaveLength(1);
  });
});
