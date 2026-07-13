import type { TaiwanInstitutionalInvestorsResponse } from "@shared/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError, fetchTaiwanInstitutionalInvestors } from "../../services/api/client";
import { InstitutionalInvestorsPanel } from "./InstitutionalInvestorsPanel";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchTaiwanInstitutionalInvestors: vi.fn(),
  };
});

const samplePayload: TaiwanInstitutionalInvestorsResponse = {
  stockId: "TW:2330",
  symbol: "2330",
  market: "TWSE",
  latestDate: "2026-07-06",
  summary: {
    foreignNetLots: 12345,
    investmentTrustNetLots: 0,
    dealerNetLots: -800,
    totalNetLots: 11545,
  },
  history: [
    {
      date: "2026-07-05",
      foreignNetLots: -200,
      investmentTrustNetLots: 0,
      dealerNetLots: 100,
      totalNetLots: -100,
    },
    {
      date: "2026-07-06",
      foreignNetLots: 12345,
      investmentTrustNetLots: 0,
      dealerNetLots: -800,
      totalNetLots: 11545,
    },
  ],
  sharesPerLot: 1000,
  source: "TWSE T86 daily institutional data",
};

function renderPanel(stockId = "TW:2330", language: "en" | "zh-TW" = "zh-TW") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <InstitutionalInvestorsPanel stockId={stockId} />
        </LanguageProvider>
      </QueryClientProvider>,
    ),
  };
}

describe("InstitutionalInvestorsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("calls the institutional API with stockId and days=90", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue(samplePayload);

    renderPanel("TW:2330");

    await waitFor(() => {
      expect(fetchTaiwanInstitutionalInvestors).toHaveBeenCalledWith("TW:2330", 90);
    });
    expect(await screen.findByTestId("tw-institutional-summary")).toBeInTheDocument();
  });

  it("renders summary rows with TW red/green classes and signed 張 formatting", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue(samplePayload);

    renderPanel();

    const summary = await screen.findByTestId("tw-institutional-summary");
    expect(summary).toHaveTextContent("三大法人買賣超");
    expect(summary).toHaveTextContent("+12,345 張");
    expect(summary).toHaveTextContent("0 張");
    expect(summary).toHaveTextContent("-800 張");
    expect(summary).toHaveTextContent("+11,545 張");

    const values = summary.querySelectorAll("dd");
    expect(values[0]).toHaveClass("app-tw-market-positive");
    expect(values[1]).toHaveClass("app-tw-market-neutral");
    expect(values[2]).toHaveClass("app-tw-market-negative");
    expect(values[3]).toHaveClass("app-tw-market-positive");

    expect(screen.getByLabelText("外資買賣超(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("投信買賣超(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("自營商買賣超(張)")).toBeInTheDocument();
  });

  it("renders English lot units and chart titles when language is en", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue(samplePayload);

    renderPanel("TW:2330", "en");

    const summary = await screen.findByTestId("tw-institutional-summary");
    await waitFor(() => {
      expect(summary).toHaveTextContent("Institutional net activity");
    });
    expect(summary).toHaveTextContent("+12,345 lots");
    expect(summary).toHaveTextContent("0 lots");
    expect(summary).toHaveTextContent("-800 lots");
    expect(screen.getByLabelText("Foreign net lots")).toBeInTheDocument();
    expect(screen.getByLabelText("Investment trust net lots")).toBeInTheDocument();
    expect(screen.getByLabelText("Dealer net lots")).toBeInTheDocument();
  });

  it("uses backend totalNetLots instead of summing history locally", async () => {
    const payload: TaiwanInstitutionalInvestorsResponse = {
      ...samplePayload,
      summary: {
        foreignNetLots: 100,
        investmentTrustNetLots: 200,
        dealerNetLots: 300,
        // Deliberately not equal to foreign+trust+dealer so a frontend sum would fail.
        totalNetLots: 999,
      },
    };
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue(payload);

    renderPanel();

    const summary = await screen.findByTestId("tw-institutional-summary");
    expect(summary).toHaveTextContent("+999 張");
    expect(summary).not.toHaveTextContent("+600 張");
  });

  it("keeps summary visible and shows empty chart copy when a series has no finite points", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue({
      ...samplePayload,
      history: [
        {
          date: "2026-07-06",
          foreignNetLots: 12345,
          investmentTrustNetLots: Number.NaN,
          dealerNetLots: -800,
          totalNetLots: 11545,
        },
      ],
    });

    renderPanel();

    expect(await screen.findByTestId("tw-institutional-summary")).toHaveTextContent("+12,345 張");
    expect(screen.getByTestId("tw-institutional-chart-empty")).toHaveTextContent(
      "此區間沒有可繪製的法人資料。",
    );
    expect(screen.getByLabelText("外資買賣超(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("自營商買賣超(張)")).toBeInTheDocument();
  });

  it("shows a loading state while the section is fetching", () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockImplementation(
      () => new Promise(() => undefined),
    );

    renderPanel();

    expect(screen.getByText("載入法人動態中")).toBeInTheDocument();
  });

  it("shows empty copy when summary and history are empty", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue({
      ...samplePayload,
      latestDate: null,
      summary: null,
      history: [],
    });

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("目前沒有可顯示的三大法人資料。")).toBeInTheDocument();
    });
  });

  it("shows empty copy for any 404 without depending on English detail text", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockRejectedValue(
      new ApiError("not found", {
        kind: "not_found",
        status: 404,
        detail: "localized backend message",
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("目前沒有可顯示的三大法人資料。")).toBeInTheDocument();
    });
  });

  it("shows unavailable notice for 502 upstream failures", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockRejectedValue(
      new ApiError("Taiwan institutional data is temporarily unavailable from official sources.", {
        kind: "unavailable",
        status: 502,
        detail: "Taiwan institutional data is temporarily unavailable from official sources.",
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
      expect(screen.getByText(/法人動態目前無法從資料提供方取得/)).toBeInTheDocument();
    });
  });

  it("shows a compact stale notice when cached payload exists and refetch fails", async () => {
    vi.mocked(fetchTaiwanInstitutionalInvestors).mockResolvedValue(samplePayload);
    const { queryClient } = renderPanel();

    await screen.findByTestId("tw-institutional-summary");

    vi.mocked(fetchTaiwanInstitutionalInvestors).mockRejectedValue(
      new ApiError("refresh failed", {
        kind: "unavailable",
        status: 502,
        detail: "refresh failed",
      }),
    );

    await queryClient.invalidateQueries({ queryKey: ["institutional-investors", "TW:2330", 90] });

    await waitFor(() => {
      expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    });
    expect(screen.getByTestId("tw-institutional-summary")).toHaveTextContent("+12,345 張");
  });
});
