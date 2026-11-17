import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError } from "../../services/api/client";
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
  opts?: { empty?: boolean; language?: "en" | "zh-TW"; error?: unknown },
) {
  window.localStorage.setItem("stock-ticker-language", opts?.language ?? "zh-TW");
  const view = render(
    <LanguageProvider>
      <TechnicalResultsPanel
        results={results}
        empty={opts?.empty}
        error={opts?.error}
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
    {
      stockId: "TW:2317",
      name: "鴻海",
      overallStageResult: "waiting_for_history",
      monthly: null,
      daily: null,
      rejectionReason: "history_budget_exhausted",
      insufficientHistory: null,
    },
    {
      stockId: "TW:2382",
      name: "廣達",
      overallStageResult: "insufficient_history",
      monthly: null,
      daily: null,
      insufficientHistory: {
        reason: "history_budget_exhausted",
        requiredBars: 35,
        availableBars: null,
        timeframe: "monthly",
      },
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
    expect(screen.getAllByText(/DIF 1\.23/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Signal 0\.99/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/OSC 0\.25/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/K 72\.16/).length).toBeGreaterThan(0);
  });

  it("shows insufficient-history reason with known bars", async () => {
    await renderPanel(sampleResults);
    const reasons = screen.getAllByTestId("tw-research-tech-insufficient-reason");
    expect(
      reasons.some((el) => el.textContent?.includes("歷史資料不足：需要 35 根，現有 12 根")),
    ).toBe(true);
  });

  it("shows insufficient-history unknown available bars", async () => {
    await renderPanel(sampleResults);
    const reasons = screen.getAllByTestId("tw-research-tech-insufficient-reason");
    expect(
      reasons.some((el) =>
        el.textContent?.includes("歷史資料不足：需要 35 根，現有筆數未知"),
      ),
    ).toBe(true);
  });

  it("shows waiting-for-history chip/text", async () => {
    await renderPanel(sampleResults);
    expect(screen.getAllByText("等待歷史資料預算").length).toBeGreaterThan(0);
  });

  it("shows symbol-error chip instead of pending", async () => {
    await renderPanel({
      ...sampleResults,
      candidates: [
        {
          stockId: "TW:2303",
          name: "聯電",
          overallStageResult: "error",
          rejectionReason: "symbol_error:TimeoutError",
          monthly: null,
          daily: null,
        },
      ],
    });
    expect(screen.getAllByText("標的錯誤").length).toBeGreaterThan(0);
    expect(screen.queryByText("待分析")).not.toBeInTheDocument();
  });

  it("shows daily-not-run reason", async () => {
    await renderPanel(sampleResults);
    const reasons = screen.getAllByTestId("tw-research-tech-daily-not-run-reason");
    expect(reasons[0]).toHaveTextContent("未執行：月線未通過");
  });

  it("shows daily-skipped count separately from calculated/verified", async () => {
    await renderPanel({
      ...sampleResults,
      calculatedCount: 2,
      verifiedCount: 2,
      dailySkippedCount: 1,
      errorCount: 0,
      deferredCount: 0,
      unknownCount: 0,
      waitingForHistoryCount: 1,
      insufficientHistoryCount: 2,
      pendingCount: 0,
      processedCount: 5,
    });
    const counts = screen.getByTestId("tw-research-technical-results-counts");
    expect(counts).toHaveTextContent(/已計算 2/);
    expect(counts).toHaveTextContent(/已驗證 2/);
    expect(counts).toHaveTextContent(/日線略過 1/);
  });

  it("shows provenance unavailable for missing source and known source when present", async () => {
    await renderPanel({
      ...sampleResults,
      candidates: [
        {
          stockId: "TW:2330",
          name: "台積電",
          overallStageResult: "pass",
          monthly: {
            asOf: "2024-06",
            dif: 1.2,
            signal: 0.9,
            osc: 0.3,
            k: 70,
            d: 60,
            barCount: 40,
            quality: "ok",
            passStatus: "pass",
            sourceProvider: "twse",
            historyCacheLocation: "postgresql_daily_price_bars",
            historyOrigin: "postgresql_cache",
            firstBarAsOf: "2020-01-01",
            lastBarAsOf: "2024-05-31",
          },
          daily: {
            asOf: "2024-06-28",
            dif: 2,
            signal: 1,
            osc: 1,
            k: 75,
            d: 65,
            barCount: 100,
            quality: "ok",
            passStatus: "pass",
          },
        },
      ],
    });
    const blocks = screen.getAllByTestId("tw-research-tech-provenance");
    expect(blocks[0]).toHaveTextContent(/source twse/);
    expect(blocks[0]).toHaveTextContent(/cache postgresql_daily_price_bars/);
    expect(blocks[1]).toHaveTextContent(/source 此已存快照不可用/);
  });

  it("shows empty state for 404 / no data", async () => {
    await renderPanel(null, {
      empty: true,
      error: new ApiError("missing", {
        status: 404,
        detail: "technical_scan_not_found",
        kind: "not_found",
      }),
    });
    expect(screen.getByTestId("tw-research-technical-results-empty")).toHaveTextContent(
      /尚無技術分析結果/,
    );
    expect(screen.queryByTestId("tw-research-technical-results-error")).not.toBeInTheDocument();
  });

  it("shows error notification for 503 without empty state", async () => {
    await renderPanel(null, {
      empty: true,
      error: new ApiError("unavailable", {
        status: 503,
        detail: "technical_scan_lookup_failed",
        kind: "server",
      }),
    });
    expect(screen.getByTestId("tw-research-technical-results-error")).toHaveTextContent(
      /暫時無法載入技術結果/,
    );
    expect(screen.queryByTestId("tw-research-technical-results-empty")).not.toBeInTheDocument();
  });
});
