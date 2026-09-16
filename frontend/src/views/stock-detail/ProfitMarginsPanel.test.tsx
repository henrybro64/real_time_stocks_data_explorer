import type { TaiwanProfitabilityResponse } from "@shared/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError, fetchTaiwanProfitability } from "../../services/api/client";
import { ProfitMarginsPanel } from "./ProfitMarginsPanel";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchTaiwanProfitability: vi.fn(),
  };
});

const samplePayload: TaiwanProfitabilityResponse = {
  stockId: "TW:2330",
  quarters: 12,
  asOf: "2025-09-30",
  source: "yahoo_finance_quarterly_income",
  stale: false,
  points: [
    {
      fiscalQuarter: "2025Q2",
      periodEnd: "2025-06-30",
      grossMarginPercent: null,
      operatingMarginPercent: 20,
      pretaxMarginPercent: 18,
      netMarginPercent: 12,
      quarterEndClose: null,
    },
    {
      fiscalQuarter: "2025Q3",
      periodEnd: "2025-09-30",
      grossMarginPercent: 54.12,
      operatingMarginPercent: 42.3,
      pretaxMarginPercent: 43.1,
      netMarginPercent: 38.2,
      quarterEndClose: null,
    },
  ],
};

function renderPanel(active = true, language: "en" | "zh-TW" = "zh-TW") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <ProfitMarginsPanel active={active} stockId="TW:2330" />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("ProfitMarginsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("does not fetch when inactive", async () => {
    renderPanel(false);
    await waitFor(() => {
      expect(fetchTaiwanProfitability).not.toHaveBeenCalled();
    });
  });

  it("fetches and renders ZH labels when active", async () => {
    vi.mocked(fetchTaiwanProfitability).mockResolvedValue(samplePayload);
    renderPanel(true, "zh-TW");
    await waitFor(() => {
      expect(fetchTaiwanProfitability).toHaveBeenCalledWith("TW:2330", 12);
    });
    expect(await screen.findByTestId("tw-profit-margins-summary")).toHaveTextContent("財報三率");
    expect(screen.getAllByText("毛利率(%)").length).toBeGreaterThan(0);
  });

  it("renders EN labels and chart with null gross gap", async () => {
    vi.mocked(fetchTaiwanProfitability).mockResolvedValue(samplePayload);
    renderPanel(true, "en");
    expect(await screen.findByText("Profitability margins")).toBeInTheDocument();
    expect(screen.getByTestId("tw-multi-series-chart")).toBeInTheDocument();
  });

  it("shows an error notice on failure", async () => {
    vi.mocked(fetchTaiwanProfitability).mockRejectedValue(
      new ApiError("upstream down", { status: 502, kind: "unavailable" }),
    );
    renderPanel(true, "en");
    expect(await screen.findByText(/Profitability margins/i)).toBeInTheDocument();
  });

  it("shows localized validation copy instead of raw Pydantic JSON", async () => {
    vi.mocked(fetchTaiwanProfitability).mockRejectedValue(
      new ApiError('Request failed: 422 - [{"type":"literal_error"}]', {
        status: 422,
        kind: "validation",
        detail: '[{"type":"literal_error","loc":["query","quarters"]}]',
      }),
    );
    renderPanel(true, "en");
    expect(await screen.findByTestId("tw-profit-margins-validation")).toHaveTextContent(
      "Invalid reporting period. Select 8Q, 12Q, or 20Q.",
    );
    expect(screen.queryByText(/literal_error/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not save/i)).not.toBeInTheDocument();
  });

  it("shows ZH validation copy for validation failures", async () => {
    vi.mocked(fetchTaiwanProfitability).mockRejectedValue(
      new ApiError("Request failed: 400", {
        status: 400,
        kind: "validation",
        detail: "quarters must be 8, 12, or 20",
      }),
    );
    renderPanel(true, "zh-TW");
    expect(await screen.findByTestId("tw-profit-margins-validation")).toHaveTextContent(
      "無效的財報期間，請選擇 8Q、12Q 或 20Q。",
    );
    expect(screen.queryByText("無法儲存")).not.toBeInTheDocument();
  });

  it("shows stale notice and fiscal-quarter axis labels", async () => {
    vi.mocked(fetchTaiwanProfitability).mockResolvedValue({
      ...samplePayload,
      stale: true,
    });
    renderPanel(true, "en");
    expect(await screen.findByText(/Showing older data/i)).toBeInTheDocument();
    expect(screen.getByText(/2025Q2 - 2025Q3/)).toBeInTheDocument();
  });

  it("shows empty copy when no points", async () => {
    vi.mocked(fetchTaiwanProfitability).mockResolvedValue({
      ...samplePayload,
      points: [],
      asOf: null,
    });
    renderPanel(true, "en");
    expect(await screen.findByText(/No profitability margin data is available/i)).toBeInTheDocument();
  });
});
