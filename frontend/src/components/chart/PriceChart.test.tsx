import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { PriceChart } from "./PriceChart";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

describe("PriceChart", () => {
  it("shows an empty state when no candles are available", () => {
    renderWithLanguage(
      <PriceChart candles={[]} chartLayout={null} currency="USD" range="1D" />,
    );

    expect(screen.getByText("沒有可顯示的圖表資料。")).toBeInTheDocument();
  });

  it("renders chart metadata when candles and layout are available", () => {
    renderWithLanguage(
      <PriceChart
        candles={[
          {
            time: "2026-04-01T09:00:00+08:00",
            open: 100,
            high: 110,
            low: 95,
            close: 102,
            volume: 1000,
          },
          {
            time: "2026-04-01T10:00:00+08:00",
            open: 102,
            high: 120,
            low: 101,
            close: 118,
            volume: 1500,
          },
        ]}
        chartLayout={{
          minY: 102,
          maxY: 118,
          pointsPolyline: "0,100 100,0",
          points: [
            { x: 0, y: 100 },
            { x: 100, y: 0 },
          ],
          yAxisTicks: [118, 114, 110, 106, 102],
          xAxisTickIndices: [0, 1],
          firstClose: 102,
          lastClose: 118,
          trendUp: true,
        }}
        currency="USD"
        range="1D"
      />,
    );

    expect(screen.getByText("價格走勢")).toBeInTheDocument();
    expect(screen.getByText("價格（USD）")).toBeInTheDocument();
    expect(screen.getByText("日期")).toBeInTheDocument();
    expect(
      screen.getByText("將游標移到圖表上或點選，可檢視該時間點的開高低收與成交量。"),
    ).toBeInTheDocument();
  });
});
