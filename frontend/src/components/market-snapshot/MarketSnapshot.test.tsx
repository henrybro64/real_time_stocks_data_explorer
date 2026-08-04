import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CommodityQuote, FxRate } from "@shared/api";
import { LanguageProvider } from "../../app/i18n";
import { ApiError, fetchUsdTwdFxRate, fetchWtiCrudeOilQuote } from "../../services/api/client";
import { MarketSnapshot } from "./MarketSnapshot";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchUsdTwdFxRate: vi.fn(),
    fetchWtiCrudeOilQuote: vi.fn(),
  };
});

const fxOk: FxRate = {
  pair: "USD/TWD",
  rate: 31.24,
  timestamp: "2026-08-01T02:00:00Z",
  change: 0.12,
  changePercent: 0.39,
  source: "Yahoo Finance",
  status: "ok",
};

const oilOk: CommodityQuote = {
  symbol: "CL=F",
  name: "WTI Crude Oil",
  price: 68.45,
  currency: "USD",
  unit: "barrel",
  change: -0.74,
  changePercent: -1.07,
  timestamp: "2026-08-01T02:05:00Z",
  source: "Yahoo Finance",
  isMarketOpen: false,
  status: "ok",
};

function renderSnapshot(language: "zh-TW" | "en" = "zh-TW") {
  window.localStorage.setItem("stock-ticker-language", language);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <MarketSnapshot />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("MarketSnapshot", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem("stock-ticker-language", "zh-TW");
  });

  it("renders both cards with valid data", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue(fxOk);
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue(oilOk);

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-fx-value")).toHaveTextContent("31.24");
      expect(screen.getByTestId("market-snapshot-wti-value")).toHaveTextContent("68.45");
    });
    expect(screen.getByTestId("market-snapshot-section")).toBeInTheDocument();
    expect(screen.getByText("市場快覽")).toBeInTheDocument();
    expect(screen.getByText("美金／台幣匯率")).toBeInTheDocument();
    expect(screen.getByText("WTI 原油")).toBeInTheDocument();
    expect(screen.getByTestId("market-snapshot-fx-change")).toHaveTextContent("+0.12");
    expect(screen.getByTestId("market-snapshot-wti-change")).toHaveTextContent("-0.74");
    expect(screen.getByTestId("market-snapshot-section").querySelector(".app-market-snapshot-grid")).toBeTruthy();
  });

  it("renders English labels when language is en", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue(fxOk);
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue(oilOk);

    renderSnapshot("en");

    await waitFor(() => {
      expect(screen.getByText("Market Snapshot")).toBeInTheDocument();
      expect(screen.getByTestId("market-snapshot-fx-value")).toHaveTextContent("TWD");
    });
    expect(screen.getByText("USD/TWD Exchange Rate")).toBeInTheDocument();
    expect(screen.getByText("WTI Crude Oil")).toBeInTheDocument();
    expect(screen.queryByText(/marketSnapshot\./)).not.toBeInTheDocument();
    // Locale-aware timestamps must not leak zh-TW day-period markers under English UI.
    expect(screen.queryByText(/上午|下午/)).not.toBeInTheDocument();
  });

  it("shows positive, negative, and unchanged change tones", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue({
      ...fxOk,
      change: 0,
      changePercent: 0,
    });
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue({
      ...oilOk,
      change: 1.25,
      changePercent: 1.8,
    });

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-fx-change")).toHaveClass("app-price-unchanged");
      expect(screen.getByTestId("market-snapshot-wti-change").className).toContain("app-price-up");
    });
  });

  it("keeps FX card when oil fails", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue(fxOk);
    vi.mocked(fetchWtiCrudeOilQuote).mockRejectedValue(
      new ApiError("provider unavailable", { kind: "unavailable" }),
    );

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-fx-value")).toBeInTheDocument();
      expect(screen.getByTestId("market-snapshot-wti")).toBeInTheDocument();
    });
    expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
    expect(screen.queryByTestId("market-snapshot-wti-value")).not.toBeInTheDocument();
  });

  it("keeps oil card when FX fails", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockRejectedValue(
      new ApiError("provider unavailable", { kind: "unavailable" }),
    );
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue(oilOk);

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-wti-value")).toBeInTheDocument();
    });
    expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
    expect(screen.queryByTestId("market-snapshot-fx-value")).not.toBeInTheDocument();
  });

  it("treats FX fallback status as unavailable instead of a live rate", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue({
      pair: "USD/TWD",
      rate: 32,
      timestamp: "2026-08-01T02:00:00Z",
      change: null,
      changePercent: null,
      source: "Yahoo Finance",
      status: "fallback",
    });
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue(oilOk);

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-wti-value")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("market-snapshot-fx-value")).not.toBeInTheDocument();
    expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
  });

  it("marks FX stale when backend returns retained status=stale", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue({
      ...fxOk,
      status: "stale",
    });
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue({
      ...oilOk,
      status: "stale",
    });

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-fx-value")).toHaveTextContent("31.24");
      expect(screen.getAllByText("過舊")).toHaveLength(2);
      expect(screen.getAllByText("顯示較舊資料")).toHaveLength(2);
    });
  });

  it("marks stale when refresh fails after prior data", async () => {
    vi.mocked(fetchUsdTwdFxRate)
      .mockResolvedValueOnce(fxOk)
      .mockRejectedValueOnce(new ApiError("provider unavailable", { kind: "unavailable" }));
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue(oilOk);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>{children}</LanguageProvider>
      </QueryClientProvider>
    );
    render(<MarketSnapshot />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-fx-value")).toBeInTheDocument();
    });

    await queryClient.refetchQueries({ queryKey: ["usd-twd-fx-rate"] });

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-fx-value")).toHaveTextContent("31.24");
      expect(screen.getByText("過舊")).toBeInTheDocument();
      expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    });
  });

  it("shows unavailable when both initial requests fail", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockRejectedValue(
      new ApiError("provider unavailable", { kind: "unavailable" }),
    );
    vi.mocked(fetchWtiCrudeOilQuote).mockRejectedValue(
      new ApiError("provider unavailable", { kind: "unavailable" }),
    );

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getAllByText("資料來源暫時不可用")).toHaveLength(2);
    });
    expect(screen.queryByTestId("market-snapshot-fx-value")).not.toBeInTheDocument();
    expect(screen.queryByTestId("market-snapshot-wti-value")).not.toBeInTheDocument();
  });

  it("does not render raw missing translation keys", async () => {
    vi.mocked(fetchUsdTwdFxRate).mockResolvedValue(fxOk);
    vi.mocked(fetchWtiCrudeOilQuote).mockResolvedValue(oilOk);

    renderSnapshot();

    await waitFor(() => {
      expect(screen.getByTestId("market-snapshot-fx-value")).toBeInTheDocument();
    });
    expect(screen.queryByText(/marketSnapshot\./)).not.toBeInTheDocument();
  });
});
