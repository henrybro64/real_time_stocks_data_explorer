import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InstitutionalHistogramChart } from "./InstitutionalHistogramChart";

describe("InstitutionalHistogramChart", () => {
  it("renders one bar per valid history point with TW market color classes", () => {
    const { container } = render(
      <InstitutionalHistogramChart
        dateRangeLabel="2026/07/04 - 2026/07/06"
        emptyLabel="此區間沒有可繪製的法人資料。"
        language="zh-TW"
        points={[
          { date: "2026-07-04", value: 500 },
          { date: "2026-07-05", value: -200 },
          { date: "2026-07-06", value: 0 },
        ]}
        title="外資買賣超(張)"
      />,
    );

    expect(screen.getByTestId("tw-institutional-chart")).toBeInTheDocument();
    expect(screen.getByText("外資買賣超(張)")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "外資買賣超(張)" })).toBeInTheDocument();
    expect(container.querySelector(".app-tw-inst-chart-zero-line")).toBeInTheDocument();

    const bars = container.querySelectorAll("rect");
    expect(bars).toHaveLength(3);
    expect(bars[0]).toHaveClass("app-tw-market-bar-positive");
    expect(bars[1]).toHaveClass("app-tw-market-bar-negative");
    expect(bars[2]).toHaveClass("app-tw-market-bar-neutral");
    expect(bars[2]).toHaveAttribute("height", "0");
    expect(bars[2]).toHaveAttribute("y", "50");
  });

  it("keeps zero-value bars on the baseline with no positive extent", () => {
    const { container } = render(
      <InstitutionalHistogramChart
        dateRangeLabel="2026/07/06 - 2026/07/06"
        emptyLabel="此區間沒有可繪製的法人資料。"
        language="zh-TW"
        points={[{ date: "2026-07-06", value: 0 }]}
        title="投信買賣超(張)"
      />,
    );

    const bar = container.querySelector("rect");
    expect(bar).toHaveClass("app-tw-market-bar-neutral");
    expect(bar).toHaveAttribute("height", "0");
    expect(bar).toHaveAttribute("y", "50");
  });

  it("preserves original timeline slots when values are missing", () => {
    const { container } = render(
      <InstitutionalHistogramChart
        dateRangeLabel="2026/07/04 - 2026/07/06"
        emptyLabel="No plottable institutional history for this range."
        language="en"
        points={[
          { date: "2026-07-04", value: 500 },
          { date: "2026-07-05", value: Number.NaN },
          { date: "2026-07-06", value: -200 },
        ]}
        title="Foreign net lots"
      />,
    );

    const bars = container.querySelectorAll("rect");
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveClass("app-tw-market-bar-positive");
    expect(bars[1]).toHaveClass("app-tw-market-bar-negative");

    const firstX = Number(bars[0]?.getAttribute("x"));
    const secondX = Number(bars[1]?.getAttribute("x"));
    // With 3 timeline slots, the second plotted bar (index 2) must sit after the midpoint,
    // not compress next to the first bar as if only two points existed.
    expect(secondX).toBeGreaterThan(50);
    expect(secondX).toBeGreaterThan(firstX + 20);

    const axisLabels = container.querySelectorAll(".app-tw-inst-chart-x-axis span");
    expect(axisLabels[0]).toHaveTextContent("7/4");
    expect(axisLabels[1]).toHaveTextContent("7/6");
  });

  it("skips non-finite values without crashing", () => {
    const { container } = render(
      <InstitutionalHistogramChart
        dateRangeLabel="2026/07/04 - 2026/07/05"
        emptyLabel="No plottable institutional history for this range."
        language="en"
        points={[
          { date: "2026-07-04", value: Number.NaN },
          { date: "2026-07-05", value: 1200 },
          { date: "2026-07-06", value: Number.POSITIVE_INFINITY },
        ]}
        title="Foreign net lots"
      />,
    );

    expect(container.querySelectorAll("rect")).toHaveLength(1);
    expect(container.querySelector("rect")).toHaveClass("app-tw-market-bar-positive");
  });

  it("shows an empty chart message when all points are invalid", () => {
    render(
      <InstitutionalHistogramChart
        dateRangeLabel=""
        emptyLabel="此區間沒有可繪製的法人資料。"
        language="zh-TW"
        points={[
          { date: "2026-07-04", value: Number.NaN },
          { date: "2026-07-05", value: Number.POSITIVE_INFINITY },
        ]}
        title="投信買賣超(張)"
      />,
    );

    expect(screen.getByTestId("tw-institutional-chart-empty")).toHaveTextContent(
      "此區間沒有可繪製的法人資料。",
    );
    expect(screen.getByText("投信買賣超(張)")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
