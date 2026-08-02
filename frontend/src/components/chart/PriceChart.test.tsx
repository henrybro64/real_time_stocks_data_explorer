import { render, screen } from "@testing-library/react";
import type { MovingAverageSeries } from "@shared/quote";
import { describe, expect, it } from "vitest";
import { act } from "react";

import { LanguageProvider } from "../../app/i18n";
import { PriceChart } from "./PriceChart";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

const twoCandleLayout = {
  minY: 90,
  maxY: 120,
  pointsPolyline: "0,100 100,0",
  points: [
    { x: 0, y: 100 },
    { x: 100, y: 0 },
  ],
  yAxisTicks: [120, 105, 90],
  xAxisTickIndices: [0, 1],
  firstClose: 102,
  lastClose: 118,
  trendUp: true,
};

const twoCandles = [
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
];

const sampleMovingAverages: MovingAverageSeries = {
  sma10: [
    { time: twoCandles[0].time, value: 100.12, direction: "up" },
    { time: twoCandles[1].time, value: 101.45, direction: "up" },
  ],
  sma20: [
    { time: twoCandles[0].time, value: 99.5, direction: "down" },
    { time: twoCandles[1].time, value: 98.75, direction: "down" },
  ],
  sma60: [
    { time: twoCandles[0].time, value: null, direction: null },
    { time: twoCandles[1].time, value: 95.2, direction: "flat" },
  ],
};

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

  it("renders SMA overlays and latest legend values for daily ranges", () => {
    const { container } = renderWithLanguage(
      <PriceChart
        candles={twoCandles}
        chartLayout={twoCandleLayout}
        currency="USD"
        market="US"
        movingAverages={sampleMovingAverages}
        range="1M"
      />,
    );

    expect(screen.getByTestId("price-chart")).toHaveAttribute("data-has-sma", "true");
    expect(screen.getByTestId("price-chart-sma-10")).toBeInTheDocument();
    expect(screen.getByTestId("price-chart-sma-20")).toBeInTheDocument();
    expect(screen.getByTestId("price-chart-sma-60")).toBeInTheDocument();
    expect(container.querySelectorAll(".app-chart-sma-10")).toHaveLength(1);
    expect(container.querySelectorAll(".app-chart-sma-20")).toHaveLength(1);
    expect(container.querySelectorAll(".app-chart-sma-60")).toHaveLength(1);

    const legend = screen.getByTestId("price-chart-sma-legend");
    expect(legend).toHaveTextContent("SMA10");
    expect(legend).toHaveTextContent("101.45");
    expect(legend).toHaveTextContent("SMA20");
    expect(legend).toHaveTextContent("98.75");
    expect(legend).toHaveTextContent("SMA60");
    expect(legend).toHaveTextContent("95.20");
  });

  it("hides SMA overlays when movingAverages is null", () => {
    renderWithLanguage(
      <PriceChart
        candles={twoCandles}
        chartLayout={twoCandleLayout}
        currency="USD"
        market="US"
        movingAverages={null}
        range="1D"
      />,
    );

    expect(screen.getByTestId("price-chart")).toHaveAttribute("data-has-sma", "false");
    expect(screen.queryByTestId("price-chart-sma-legend")).not.toBeInTheDocument();
    expect(screen.queryByTestId("price-chart-sma-10")).not.toBeInTheDocument();
    expect(screen.queryByTestId("price-chart-sma-20")).not.toBeInTheDocument();
    expect(screen.queryByTestId("price-chart-sma-60")).not.toBeInTheDocument();
  });

  it("does not connect SMA paths across null gaps and avoids NaN coordinates", () => {
    const movingAverages: MovingAverageSeries = {
      sma10: [
        { time: twoCandles[0].time, value: 100, direction: "up" },
        { time: twoCandles[1].time, value: null, direction: null },
      ],
      sma20: [
        { time: twoCandles[0].time, value: null, direction: null },
        { time: twoCandles[1].time, value: 110, direction: "up" },
      ],
      sma60: [
        { time: twoCandles[0].time, value: 90, direction: "flat" },
        { time: twoCandles[1].time, value: 92, direction: "up" },
      ],
    };
    const threeCandles = [
      ...twoCandles,
      {
        time: "2026-04-01T11:00:00+08:00",
        open: 118,
        high: 119,
        low: 109,
        close: 112,
        volume: 1400,
      },
    ];
    const gapped: MovingAverageSeries = {
      sma10: [
        { time: threeCandles[0].time, value: 100, direction: "up" },
        { time: threeCandles[1].time, value: null, direction: null },
        { time: threeCandles[2].time, value: 112, direction: "up" },
      ],
      sma20: movingAverages.sma20.concat([{ time: threeCandles[2].time, value: 111, direction: "up" }]),
      sma60: movingAverages.sma60.concat([{ time: threeCandles[2].time, value: 93, direction: "up" }]),
    };

    const { container } = renderWithLanguage(
      <PriceChart
        candles={threeCandles}
        chartLayout={{
          minY: 90,
          maxY: 120,
          pointsPolyline: "0,72 50,8 100,32",
          points: [
            { x: 0, y: 72 },
            { x: 50, y: 8 },
            { x: 100, y: 32 },
          ],
          yAxisTicks: [120, 105, 90],
          xAxisTickIndices: [0, 1, 2],
          firstClose: 102,
          lastClose: 112,
          trendUp: true,
        }}
        currency="USD"
        market="US"
        movingAverages={gapped}
        range="3M"
      />,
    );

    const sma10Polylines = container.querySelectorAll('[data-testid="price-chart-sma-10"] polyline');
    expect(sma10Polylines).toHaveLength(2);
    for (const polyline of sma10Polylines) {
      expect(polyline.getAttribute("points")).not.toMatch(/NaN/);
      expect(polyline).toHaveAttribute("vector-effect", "non-scaling-stroke");
      expect(polyline).toHaveAttribute("pointer-events", "none");
    }
  });

  it("updates the SMA legend and tooltip for the hovered candle", () => {
    const { container } = renderWithLanguage(
      <PriceChart
        candles={twoCandles}
        chartLayout={twoCandleLayout}
        currency="USD"
        market="US"
        movingAverages={sampleMovingAverages}
        range="1M"
      />,
    );

    const wrap = container.querySelector(".app-chart-svg-wrap");
    expect(wrap).not.toBeNull();
    act(() => {
      Object.defineProperty(wrap!, "getBoundingClientRect", {
        configurable: true,
        value: () =>
          ({
            left: 0,
            top: 0,
            width: 200,
            height: 100,
            right: 200,
            bottom: 100,
            x: 0,
            y: 0,
            toJSON: () => ({}),
          }) as DOMRect,
      });
      wrap?.dispatchEvent(
        new MouseEvent("pointermove", {
          bubbles: true,
          clientX: 10,
          clientY: 40,
        }),
      );
    });

    const legend = screen.getByTestId("price-chart-sma-legend");
    expect(legend).toHaveTextContent("100.12");
    expect(legend).toHaveTextContent("99.50");
    expect(legend).not.toHaveTextContent("95.20");
    expect(screen.getByTestId("price-chart-tooltip-sma10")).toHaveTextContent("100.12");
    expect(screen.getByTestId("price-chart-tooltip-sma20")).toHaveTextContent("99.50");
    expect(screen.queryByTestId("price-chart-tooltip-sma60")).not.toBeInTheDocument();
  });

  it("uses market price-tone classes for SMA direction arrows", () => {
    const { container: usContainer } = renderWithLanguage(
      <PriceChart
        candles={twoCandles}
        chartLayout={twoCandleLayout}
        currency="USD"
        market="US"
        movingAverages={sampleMovingAverages}
        range="1M"
      />,
    );
    expect(usContainer.querySelector(".app-chart-sma-legend-arrow.app-price-up")).not.toBeNull();
    expect(usContainer.querySelector(".app-chart-sma-legend-arrow.app-price-down")).not.toBeNull();
    expect(usContainer.querySelector(".app-chart-sma-legend-arrow.app-price-convention-us")).not.toBeNull();

    const { container: twContainer } = renderWithLanguage(
      <PriceChart
        candles={twoCandles}
        chartLayout={twoCandleLayout}
        currency="TWD"
        market="TW"
        movingAverages={sampleMovingAverages}
        range="1M"
      />,
    );
    expect(twContainer.querySelector(".app-chart-sma-legend-arrow.app-price-convention-taiwan")).not.toBeNull();
  });
});
