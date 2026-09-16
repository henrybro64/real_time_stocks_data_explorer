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
  source: "yfinance_quarterly_income",
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
