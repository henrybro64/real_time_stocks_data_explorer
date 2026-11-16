import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import type { TwResearchTechnicalResults } from "../../services/api/twResearch";
import {
  TechnicalResultsPanel,
  formatIndicatorValue,
} from "./TechnicalResultsPanel";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

afterEach(() => {
  window.localStorage.clear();
});

async function renderPanel(
  results: TwResearchTechnicalResults | null,
  opts?: { empty?: boolean; language?: "en" | "zh-TW" },
) {
  window.localStorage.setItem("stock-ticker-language", opts?.language ?? "zh-TW");
  const view = render(
    <LanguageProvider>
      <TechnicalResultsPanel
        results={results}
        empty={opts?.empty}
        phase2Message="分析中…"
      />
    </LanguageProvider>,
  );
  await waitFor(() => {
    expect(screen.getByTestId("tw-research-technical-results")).toBeInTheDocument();
  });
  return view;
}

const sampleResults: TwResearchTechnicalResults = {
  technicalScanId: "scan_1",
  scanStatus: "running",
  calculationVersion: "macd12-26-9_kd9-3-3_monthly_first_v1",
  priceBasis: "unadjusted_close",
  candidates: [
    {
      stockId: "TW:2330",
      name: "台積電",
      overallStageResult: "pass",
      monthly: {
        asOf: "2024-06",
        dif: 1.2345,
        signal: 0.9876,
        osc: 0.2469,
        k: 72.159,
        d: 65.4,
        barCount: 48,
        quality: "ok",
        passStatus: "pass",
      },
      daily: {
        asOf: "2024-06-28",
        dif: 2.5,
        signal: 1.1,
        osc: 1.4,
        k: 80,
        d: 70,
        barCount: 120,
        quality: "ok",
        passStatus: "pass",
      },
    },
    {
      stockId: "TW:2454",
      name: "聯發科",
      overallStageResult: "insufficient_history",
      monthly: {
        asOf: null,
        dif: null,
        signal: null,
        osc: null,
        k: null,
        d: null,
        barCount: 12,
        quality: "insufficient_history",
        passStatus: "unknown",
      },
      daily: null,
      insufficientHistory: {
        reason: "insufficient_history",
        requiredBars: 35,
        availableBars: 12,
        timeframe: "monthly",
      },
    },
    {
      stockId: "TW:2303",
      name: "聯電",
      overallStageResult: "daily_not_run",
      monthly: {
        asOf: "2024-05",
        dif: -0.5,
        signal: -0.2,
        osc: -0.3,
        k: 20,
        d: 25,
        barCount: 40,
        quality: "ok",
        passStatus: "fail",
      },
      daily: null,
      dailyNotRunReason: "monthly_gate_failed",
    },
  ],
};

describe("TechnicalResultsPanel (#361)", () => {
  it("formats indicator values to 2 decimals", () => {
    expect(formatIndicatorValue(1.2345)).toBe("1.23");
    expect(formatIndicatorValue(null)).toBe("—");
  });

  it("renders values with 2 decimals", async () => {
    await renderPanel(sampleResults);
    // Desktop table + mobile cards both render in the DOM.
    expect(screen.getAllByText(/DIF 1\.23/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Signal 0\.99/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/OSC 0\.25/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/K 72\.16/).length).toBeGreaterThan(0);
  });

  it("shows insufficient-history reason", async () => {
    await renderPanel(sampleResults);
    const reasons = screen.getAllByTestId("tw-research-tech-insufficient-reason");
    expect(reasons[0]).toHaveTextContent("歷史資料不足：需要 35 根，現有 12 根");
  });

  it("shows daily-not-run reason", async () => {
    await renderPanel(sampleResults);
    const reasons = screen.getAllByTestId("tw-research-tech-daily-not-run-reason");
    expect(reasons[0]).toHaveTextContent("未執行：月線未通過");
  });

  it("shows empty state", async () => {
    await renderPanel(null, { empty: true });
    expect(screen.getByTestId("tw-research-technical-results-empty")).toHaveTextContent(
      /尚無技術分析結果/,
    );
  });
});
