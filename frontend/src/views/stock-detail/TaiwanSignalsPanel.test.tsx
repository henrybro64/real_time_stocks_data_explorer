import type {
  TaiwanOwnershipConcentrationResponse,
  TaiwanProfitabilityResponse,
} from "@shared/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import {
  fetchTaiwanInstitutionalInvestors,
  fetchTaiwanMarginShort,
  fetchTaiwanOwnershipConcentration,
  fetchTaiwanProfitability,
} from "../../services/api/client";
import { TaiwanSignalsPanel } from "./TaiwanSignalsPanel";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchTaiwanInstitutionalInvestors: vi.fn(),
    fetchTaiwanMarginShort: vi.fn(),
    fetchTaiwanOwnershipConcentration: vi.fn(),
    fetchTaiwanProfitability: vi.fn(),
  };
});

const ownershipPayload: TaiwanOwnershipConcentrationResponse = {
  stockId: "TW:2330",
  range: "1Y",
  asOf: "2026-09-11",
  source: "tdcc_portal+twse_mi_qfiis+quote_history",
  stale: false,
  currency: "TWD",
  dataStatus: {
    tdcc: { status: "available" },
    foreignOwnership: { status: "available" },
    weeklyClose: { status: "available" },
  },
  points: [
    {
      date: "2026-06-12",
      holders400LotsOrMorePercent: 61.5,
      foreignOwnershipPercent: 17.5,
      investmentTrustOwnershipPercent: null,
      dealerOwnershipPercent: null,
      directorOwnershipPercent: null,
      weeklyClose: 80,
    },
    {
      date: "2026-09-11",
      holders400LotsOrMorePercent: 63.46,
      foreignOwnershipPercent: 18.21,
      investmentTrustOwnershipPercent: null,
      dealerOwnershipPercent: null,
      directorOwnershipPercent: null,
      weeklyClose: 88.1,
    },
  ],
};

const profitabilityPayload: TaiwanProfitabilityResponse = {
  stockId: "TW:2330",
  quarters: 12,
  asOf: "2025-09-30",
  source: "yahoo_finance_quarterly_income",
  stale: false,
  points: [
    {
      fiscalQuarter: "2025Q2",
      periodEnd: "2025-06-30",
      grossMarginPercent: 50,
      operatingMarginPercent: 40,
      pretaxMarginPercent: 38,
      netMarginPercent: 30,
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

function renderPanel(language: "en" | "zh-TW" = "zh-TW") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <TaiwanSignalsPanel stockId="TW:2330" />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("TaiwanSignalsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue({
      stockId: "TW:2330",
      symbol: "2330",
      market: "TWSE",
      latestDate: null,
      summary: null,
      history: [],
      sharesPerLot: 1000,
      source: "test",
    });
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      stockId: "TW:2330",
      symbol: "2330",
      market: "TWSE",
      latestDate: null,
      summary: null,
      history: [],
      sharesPerLot: 1000,
      source: "test",
    });
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue(ownershipPayload);
    vi.mocked(fetchTaiwanProfitability).mockResolvedValue(profitabilityPayload);
  });

  it("shows four TW signal tabs including ownership and profitability", async () => {
    renderPanel("en");
    expect(await screen.findByText("Institutional activity")).toBeInTheDocument();
    expect(screen.getByText("Margin / short changes")).toBeInTheDocument();
    expect(screen.getByText("Ownership concentration")).toBeInTheDocument();
    expect(screen.getByText("Profitability margins")).toBeInTheDocument();
  });

  it("does not call ownership or profitability APIs until those tabs are selected", async () => {
    renderPanel();
    await waitFor(() => {
      expect(fetchTaiwanInstitutionalInvestors).toHaveBeenCalled();
    });
    expect(fetchTaiwanOwnershipConcentration).not.toHaveBeenCalled();
    expect(fetchTaiwanProfitability).not.toHaveBeenCalled();
  });

  it("lazy-loads ownership when the ownership tab is selected", async () => {
    renderPanel("zh-TW");
    fireEvent.click(screen.getByText("籌碼集中度"));
    await waitFor(() => {
      expect(fetchTaiwanOwnershipConcentration).toHaveBeenCalledWith("TW:2330", "1Y");
    });
    expect(await screen.findByTestId("tw-ownership-summary")).toBeInTheDocument();
  });

  it("lazy-loads profitability when the margins tab is selected", async () => {
    renderPanel("en");
    fireEvent.click(screen.getByText("Profitability margins"));
    await waitFor(() => {
      expect(fetchTaiwanProfitability).toHaveBeenCalledWith("TW:2330", 12);
    });
    expect(await screen.findByTestId("tw-profit-margins-summary")).toBeInTheDocument();
  });

  it("does not refetch profitability when switching away and back within staleTime", async () => {
    renderPanel("en");
    fireEvent.click(screen.getByRole("tab", { name: "Profitability margins" }));
    await waitFor(() => {
      expect(fetchTaiwanProfitability).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByRole("tab", { name: "Institutional activity" }));
    fireEvent.click(screen.getByRole("tab", { name: "Profitability margins" }));
    await waitFor(() => {
      expect(screen.getByTestId("tw-profit-margins-summary")).toBeInTheDocument();
    });
    expect(fetchTaiwanProfitability).toHaveBeenCalledTimes(1);
  });
});
