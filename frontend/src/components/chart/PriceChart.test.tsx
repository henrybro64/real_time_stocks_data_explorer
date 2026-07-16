import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { act } from "react";

import { LanguageProvider } from "../../app/i18n";
import { PriceChart } from "./PriceChart";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

describe("PriceChart", () => {
  it("shows an empty state when no candles are available", () => {
    renderWithLanguage(
      <PriceChart candles={[]} chartLayout={null} currency="USD" market="US" range="1D" />,
    );

    expect(screen.getByText("沒有可顯示的圖表資料。")).toBeInTheDocument();
  });

  it("shows an empty state when candles exist but chart layout is missing", () => {
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
        ]}
        chartLayout={null}
        currency="USD"
        market="US"
        range="1D"
      />,
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
          minY: 95,
          maxY: 120,
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
        market="US"
        range="1D"
      />,
    );

    expect(screen.getByText("價格走勢")).toBeInTheDocument();
    expect(screen.getByText("價格（USD）")).toBeInTheDocument();
    expect(screen.getByText("日期")).toBeInTheDocument();
    expect(
      screen.getByText("將游標移到圖表上、點選或拖曳，可檢視該時間點的開高低收與成交量。"),
    ).toBeInTheDocument();
  });

  it("clears the active crosshair after an orientation change", () => {
    const { container } = renderWithLanguage(
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
          minY: 95,
          maxY: 120,
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
        market="US"
        range="1D"
      />,
    );

    const wrap = container.querySelector(".app-chart-svg-wrap");
    expect(wrap).not.toBeNull();
    act(() => {
      wrap?.dispatchEvent(
        new MouseEvent("pointerdown", {
          bubbles: true,
          clientX: 40,
          clientY: 40,
        }),
      );
    });
    expect(container.querySelector(".app-chart-candle-body-active")).not.toBeNull();

    act(() => {
      window.dispatchEvent(new Event("orientationchange"));
    });

    expect(container.querySelector(".app-chart-candle-body-active")).toBeNull();
    expect(container.querySelector(".app-chart-panel")?.getAttribute("data-viewport-epoch")).toBe("1");
  });

  it("renders candlestick bodies for each candle in the series", () => {
    const { container } = renderWithLanguage(
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
          {
            time: "2026-04-01T11:00:00+08:00",
            open: 118,
            high: 119,
            low: 109,
            close: 112,
            volume: 1400,
          },
        ]}
        chartLayout={{
          minY: 95,
          maxY: 120,
          pointsPolyline: "0,72 50,8 100,32",
          points: [
            { x: 0, y: 72 },
            { x: 50, y: 8 },
            { x: 100, y: 32 },
          ],
          yAxisTicks: [120, 114, 108, 102, 95],
          xAxisTickIndices: [0, 1, 2],
          firstClose: 102,
          lastClose: 112,
          trendUp: true,
        }}
        currency="USD"
        market="US"
        range="1D"
      />,
    );

    expect(container.querySelectorAll(".app-chart-candle-body")).toHaveLength(3);
    expect(container.querySelectorAll(".app-chart-candle-wick")).toHaveLength(3);
    expect(container.querySelector(".app-chart-panel")?.getAttribute("data-price-convention")).toBe("us");
    expect(container.querySelectorAll(".app-chart-candle-body-up")).toHaveLength(2);
    expect(container.querySelectorAll(".app-chart-candle-body-down")).toHaveLength(1);
  });

  it("uses the Taiwan price convention attribute for TW market charts", () => {
    const { container } = renderWithLanguage(
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
            high: 105,
            low: 90,
            close: 92,
            volume: 1200,
          },
        ]}
        chartLayout={{
          minY: 90,
          maxY: 110,
          pointsPolyline: "0,40 100,90",
          points: [
            { x: 0, y: 40 },
            { x: 100, y: 90 },
          ],
          yAxisTicks: [110, 100, 90],
          xAxisTickIndices: [0, 1],
          firstClose: 102,
          lastClose: 92,
          trendUp: false,
        }}
        currency="TWD"
        market="TW"
        range="1W"
      />,
    );

    expect(container.querySelector(".app-chart-panel")?.getAttribute("data-price-convention")).toBe("taiwan");
    expect(container.querySelectorAll(".app-chart-candle-body-up")).toHaveLength(1);
    expect(container.querySelectorAll(".app-chart-candle-body-down")).toHaveLength(1);
    expect(container.querySelectorAll(".app-chart-candle-wick-up")).toHaveLength(1);
    expect(container.querySelectorAll(".app-chart-candle-wick-down")).toHaveLength(1);
  });
});
