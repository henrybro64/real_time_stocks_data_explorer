import type { TaiwanOwnershipConcentrationResponse } from "@shared/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError, fetchTaiwanOwnershipConcentration } from "../../services/api/client";
import { OwnershipConcentrationPanel } from "./OwnershipConcentrationPanel";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchTaiwanOwnershipConcentration: vi.fn(),
  };
});

const samplePayload: TaiwanOwnershipConcentrationResponse = {
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
      foreignOwnershipPercent: null,
      investmentTrustOwnershipPercent: null,
      dealerOwnershipPercent: null,
      directorOwnershipPercent: null,
      weeklyClose: null,
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

function renderPanel(active = true, language: "en" | "zh-TW" = "zh-TW") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <OwnershipConcentrationPanel active={active} stockId="TW:2330" />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("OwnershipConcentrationPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("does not fetch when inactive", async () => {
    renderPanel(false);
    await waitFor(() => {
      expect(fetchTaiwanOwnershipConcentration).not.toHaveBeenCalled();
    });
  });

  it("fetches and renders ZH labels when active", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue(samplePayload);
    renderPanel(true, "zh-TW");
    await waitFor(() => {
      expect(fetchTaiwanOwnershipConcentration).toHaveBeenCalledWith("TW:2330", "1Y");
    });
    expect(await screen.findByTestId("tw-ownership-summary")).toHaveTextContent("籌碼集中度");
    expect(screen.getAllByText("400張以上持股(%)").length).toBeGreaterThan(0);
    expect(screen.getByTestId("tw-ownership-weekly-close-value")).toHaveTextContent("TWD");
    expect(screen.getByTestId("tw-ownership-notes")).toHaveTextContent(
      "目前的官方資料來源不提供投信持股比例",
    );
  });

  it("renders EN labels and currency formatting", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue(samplePayload);
    renderPanel(true, "en");
    expect(await screen.findByText("Ownership concentration")).toBeInTheDocument();
    expect(screen.getAllByText("Holders ≥400 lots (%)").length).toBeGreaterThan(0);
    expect(screen.getByTestId("tw-ownership-weekly-close-value")).toHaveTextContent("88.1 TWD");
    expect(screen.getByTestId("tw-ownership-notes")).toHaveTextContent(
      "investment-trust ownership percentages",
    );
  });

  it("shows an error notice on failure", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockRejectedValue(
      new ApiError("upstream down", { status: 502, kind: "unavailable" }),
    );
    renderPanel(true, "en");
    expect(await screen.findByText(/Ownership concentration/i)).toBeInTheDocument();
  });

  it("renders multi-series chart with dual axis when prices exist", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue(samplePayload);
    renderPanel(true);
    expect(await screen.findByTestId("tw-multi-series-chart")).toBeInTheDocument();
    expect(screen.getByTestId("tw-multi-series-right-axis")).toBeInTheDocument();
  });

  it("shows stale notice when payload.stale is true", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue({
      ...samplePayload,
      stale: true,
    });
    renderPanel(true, "en");
    expect(await screen.findByTestId("tw-ownership-summary")).toBeInTheDocument();
    expect(screen.getByText(/Showing older data/i)).toBeInTheDocument();
  });

  it("shows foreign unavailable status message in ZH", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue({
      ...samplePayload,
      source: "tdcc_portal+quote_history",
      dataStatus: {
        tdcc: { status: "available" },
        foreignOwnership: { status: "temporarily_unavailable", reason: "upstream_timeout" },
        weeklyClose: { status: "available" },
      },
      points: samplePayload.points.map((point) => ({
        ...point,
        foreignOwnershipPercent: null,
      })),
    });
    renderPanel(true, "zh-TW");
    expect(await screen.findByTestId("tw-ownership-foreign-value")).toHaveTextContent("—");
    expect(screen.getByTestId("tw-ownership-foreign-status")).toHaveTextContent(
      "外資持股資料暫時無法取得，請稍後再試。",
    );
    expect(screen.getByTestId("tw-ownership-partial-notice")).toBeInTheDocument();
  });

  it("shows weekly close unavailable status message in EN", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue({
      ...samplePayload,
      source: "tdcc_portal+twse_mi_qfiis",
      dataStatus: {
        tdcc: { status: "available" },
        foreignOwnership: { status: "available" },
        weeklyClose: { status: "temporarily_unavailable", reason: "history_unavailable" },
      },
      points: samplePayload.points.map((point) => ({
        ...point,
        weeklyClose: null,
      })),
    });
    renderPanel(true, "en");
    expect(await screen.findByTestId("tw-ownership-weekly-close-value")).toHaveTextContent("n/a");
    expect(screen.getByTestId("tw-ownership-weekly-close-status")).toHaveTextContent(
      "Weekly closing prices could not be loaded.",
    );
  });

  it("shows TDCC-only partial with not_published foreign copy", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue({
      ...samplePayload,
      source: "tdcc_portal+quote_history",
      dataStatus: {
        tdcc: { status: "available" },
        foreignOwnership: { status: "not_published", reason: "symbol_absent" },
        weeklyClose: { status: "available" },
      },
      points: samplePayload.points.map((point) => ({
        ...point,
        foreignOwnershipPercent: null,
      })),
    });
    renderPanel(true, "en");
    expect(await screen.findByTestId("tw-ownership-foreign-status")).toHaveTextContent(
      /do not publish foreign ownership/i,
    );
    expect(screen.getByText(/Source: tdcc_portal\+quote_history$/)).toBeInTheDocument();
  });

  it("shows empty copy when points are empty", async () => {
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue({
      ...samplePayload,
      points: [],
      asOf: null,
    });
    renderPanel(true, "en");
    expect(
      await screen.findByText(/No ownership concentration data is available/i),
    ).toBeInTheDocument();
  });

  it("refetches when range control changes", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    vi.mocked(fetchTaiwanOwnershipConcentration).mockResolvedValue(samplePayload);
    const user = userEvent.setup();
    renderPanel(true, "en");
    await waitFor(() => {
      expect(fetchTaiwanOwnershipConcentration).toHaveBeenCalledWith("TW:2330", "1Y");
    });
    await user.click(screen.getByRole("button", { name: "6M" }));
    await waitFor(() => {
      expect(fetchTaiwanOwnershipConcentration).toHaveBeenCalledWith("TW:2330", "6M");
    });
  });
});
