import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TaiwanSignalLineChart } from "./TaiwanSignalLineChart";

describe("TaiwanSignalLineChart", () => {
  it("renders one plotted point per finite history point", () => {
    const { container } = render(
      <TaiwanSignalLineChart
        dateRangeLabel="2026/07/04 - 2026/07/06"
        emptyLabel="此區間沒有可繪製的資券資料。"
        language="zh-TW"
        points={[
          { date: "2026-07-04", value: 31000 },
          { date: "2026-07-05", value: 31220 },
          { date: "2026-07-06", value: 31339 },
        ]}
        title="融資餘額(張)"
      />,
    );

    expect(screen.getByTestId("tw-margin-short-line-chart")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "融資餘額(張)" })).toBeInTheDocument();
    expect(container.querySelector(".app-value-chart-line")).toBeInTheDocument();
    expect(container.querySelectorAll("circle")).toHaveLength(3);
  });

  it("skips non-finite values without crashing", () => {
    const { container } = render(
      <TaiwanSignalLineChart
        dateRangeLabel="2026/07/04 - 2026/07/06"
        emptyLabel="No plottable margin / short history for this range."
        language="en"
        points={[
          { date: "2026-07-04", value: Number.NaN },
          { date: "2026-07-05", value: 120 },
          { date: "2026-07-06", value: Number.POSITIVE_INFINITY },
        ]}
        title="Short margin balance (lots)"
      />,
    );

    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelector(".app-value-chart-line")).toBeInTheDocument();
  });

  it("shows an empty message when all points are invalid", () => {
    render(
      <TaiwanSignalLineChart
        dateRangeLabel=""
        emptyLabel="此區間沒有可繪製的資券資料。"
        language="zh-TW"
        points={[
          { date: "2026-07-04", value: Number.NaN },
          { date: "2026-07-05", value: Number.POSITIVE_INFINITY },
        ]}
        title="融券餘額(張)"
      />,
    );

    expect(screen.getByTestId("tw-margin-short-line-chart-empty")).toHaveTextContent(
      "此區間沒有可繪製的資券資料。",
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
