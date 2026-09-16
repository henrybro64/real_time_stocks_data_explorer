import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TaiwanMultiSeriesLineChart } from "./TaiwanMultiSeriesLineChart";

describe("TaiwanMultiSeriesLineChart", () => {
  it("renders segmented polylines when a series has gaps", () => {
    const { container } = render(
      <TaiwanMultiSeriesLineChart
        dateRangeLabel="2025Q1 - 2025Q3"
        emptyLabel="empty"
        language="en"
        lines={[
          { key: "gross", label: "Gross", color: "#78a9ff" },
          { key: "net", label: "Net", color: "#8a3ffc" },
        ]}
        points={[
          { date: "2025-03-31", values: { gross: 50, net: 30 } },
          { date: "2025-06-30", values: { gross: null, net: 28 } },
          { date: "2025-09-30", values: { gross: 54, net: 38 } },
        ]}
        title="Margins"
      />,
    );

    expect(screen.getByTestId("tw-multi-series-chart")).toBeInTheDocument();
    expect(container.querySelectorAll("polyline").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Gross")).toBeInTheDocument();
    expect(screen.getByText("Net")).toBeInTheDocument();
  });

  it("supports negative values without crashing", () => {
    render(
      <TaiwanMultiSeriesLineChart
        dateRangeLabel="range"
        emptyLabel="empty"
        language="zh-TW"
        lines={[{ key: "net", label: "淨利率", color: "#8a3ffc" }]}
        points={[
          { date: "2025-03-31", values: { net: -5 } },
          { date: "2025-06-30", values: { net: 2 } },
        ]}
        title="負值"
      />,
    );
    expect(screen.getByTestId("tw-multi-series-chart")).toBeInTheDocument();
  });

  it("uses formatPointLabel for axis and tooltip", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TaiwanMultiSeriesLineChart
        dateRangeLabel="2025Q2 - 2025Q3"
        emptyLabel="empty"
        formatPointLabel={(point) => point.label ?? point.date}
        language="en"
        lines={[{ key: "net", label: "Net", color: "#8a3ffc" }]}
        points={[
          { date: "2025-06-30", label: "2025Q2", values: { net: 12 } },
          { date: "2025-09-30", label: "2025Q3", values: { net: 14 } },
        ]}
        title="Margins"
      />,
    );
    expect(screen.getByText("2025Q2")).toBeInTheDocument();
    expect(screen.getByText("2025Q3")).toBeInTheDocument();
    const hoverTarget = container.querySelector("rect");
    expect(hoverTarget).not.toBeNull();
    await user.hover(hoverTarget!);
    expect(screen.getByTestId("tw-multi-series-tooltip")).toHaveTextContent("2025Q2");
  });
});
