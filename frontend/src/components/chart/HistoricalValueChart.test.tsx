import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { HistoricalValueChart } from "./HistoricalValueChart";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

describe("HistoricalValueChart", () => {
  it("shows an empty state when no points are available", () => {
    renderWithLanguage(
      <HistoricalValueChart
        title="持股市值歷史（USD）"
        series={null}
        range="1M"
        onZoomIn={() => undefined}
        onZoomOut={() => undefined}
        canZoomIn={false}
        canZoomOut={true}
      />,
    );

    expect(
      screen.getByText("目前還沒有歷史資料。台灣或美國市場開盤期間會按小時累積快照。"),
    ).toBeInTheDocument();
  });

  it("renders the line chart metadata and zoom controls", () => {
    const onZoomIn = vi.fn();
    const onZoomOut = vi.fn();

    const { container } = renderWithLanguage(
      <HistoricalValueChart
        title="持股市值歷史（USD）"
        series={{
          currency: "USD",
          points: [
            {
              timestamp: "2026-05-05T01:00:00+00:00",
              sourceWindow: "TW",
              fxRate: 32,
              value: 1250,
            },
            {
              timestamp: "2026-05-05T02:00:00+00:00",
              sourceWindow: "TW",
              fxRate: 32.1,
              value: 1325,
            },
          ],
          chartLayout: {
            minY: 1250,
            maxY: 1325,
            pointsPolyline: "0,100 100,0",
            points: [
              { x: 0, y: 100 },
              { x: 100, y: 0 },
            ],
            yAxisTicks: [1325, 1306.25, 1287.5, 1268.75, 1250],
            xAxisTickIndices: [0, 1],
            firstClose: 1250,
            lastClose: 1325,
            trendUp: true,
          },
        }}
        range="1M"
        onZoomIn={onZoomIn}
        onZoomOut={onZoomOut}
        canZoomIn={true}
        canZoomOut={true}
      />,
    );

    expect(screen.getByText("持股市值歷史（USD）")).toBeInTheDocument();
    expect(screen.getByText("區間：1M")).toBeInTheDocument();
    expect(screen.getByText("放大")).toBeInTheDocument();
    expect(screen.getByText("縮小")).toBeInTheDocument();
    expect(screen.getByText("價格（USD）")).toBeInTheDocument();
    expect(screen.getByText("日期")).toBeInTheDocument();
    expect(container.querySelector(".app-value-chart-line")).toBeTruthy();

    fireEvent.click(screen.getByText("放大"));
    fireEvent.click(screen.getByText("縮小"));

    expect(onZoomIn).toHaveBeenCalledTimes(1);
    expect(onZoomOut).toHaveBeenCalledTimes(1);
  });
});
