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

  it("shows tooltip on pointer interaction for touch devices", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TaiwanMultiSeriesLineChart
        dateRangeLabel="range"
        emptyLabel="empty"
        formatPointLabel={(point) => point.label ?? point.date}
        language="en"
        lines={[{ key: "net", label: "Net", color: "#8a3ffc" }]}
        points={[
          { date: "2025-06-30", label: "2025Q2", values: { net: -5 } },
          { date: "2025-09-30", label: "2025Q3", values: { net: 14 } },
        ]}
        title="Margins"
      />,
    );
    const targets = container.querySelectorAll("rect");
    expect(targets.length).toBeGreaterThan(1);
    await user.pointer({ keys: "[TouchA>]", target: targets[1]! });
    expect(screen.getByTestId("tw-multi-series-tooltip")).toHaveTextContent("2025Q3");
    expect(screen.getByTestId("tw-multi-series-tooltip")).toHaveTextContent("14");
  });

  it("renders right axis for price series and skips nulls as zero", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TaiwanMultiSeriesLineChart
        dateRangeLabel="range"
        emptyLabel="empty"
        language="en"
        missingValueLabel="n/a"
        lines={[
          { key: "holders", label: "Holders", color: "#78a9ff", axis: "left", unit: "%" },
          { key: "close", label: "Close", color: "#8a3ffc", axis: "right", unit: "TWD" },
        ]}
        points={[
          { date: "2026-06-12", values: { holders: 61.5, close: null } },
          { date: "2026-09-11", values: { holders: 63.46, close: 8.37 } },
        ]}
        title="Ownership"
      />,
    );

    expect(screen.getByTestId("tw-multi-series-right-axis")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Ownership" })).toBeInTheDocument();
    const closeDots = container.querySelectorAll('circle[data-axis="right"]');
    expect(closeDots).toHaveLength(1);

    const targets = container.querySelectorAll("rect");
    await user.hover(targets[0]!);
    const tooltipMissing = screen.getByTestId("tw-multi-series-tooltip");
    expect(tooltipMissing).toHaveTextContent("n/a");
    expect(tooltipMissing).not.toHaveTextContent("Close: 0");

    await user.hover(targets[1]!);
    const tooltipPrice = screen.getByTestId("tw-multi-series-tooltip");
    expect(tooltipPrice).toHaveTextContent("8.37 TWD");
    expect(tooltipPrice).not.toHaveTextContent("8.4 TWD");
  });
});
