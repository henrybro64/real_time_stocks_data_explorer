import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { MacdKdIndicatorsPanel } from "./MacdKdIndicatorsPanel";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchTechnicalIndicators: vi.fn(),
  };
});

import { fetchTechnicalIndicators } from "../../services/api/client";

function renderPanel(stockId: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <MacdKdIndicatorsPanel stockId={stockId} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("MacdKdIndicatorsPanel", () => {
  beforeEach(() => {
    vi.mocked(fetchTechnicalIndicators).mockReset();
  });

  it("renders daily and monthly values for TW stocks", async () => {
    vi.mocked(fetchTechnicalIndicators).mockResolvedValue({
      stockId: "TW:2330",
      calculationVersion: "macd12-26-9_kd9-3-3_v1",
      upstreamFetch: false,
      daily: {
        timeframe: "daily",
        quality: "ok",
        asOf: "2024-06-27",
        passStatus: "pass",
        macdDif: 1.25,
        macdSignal: 0.8,
        macdHistogram: 0.45,
        kdK: 62,
        kdD: 55,
      },
      monthly: {
        timeframe: "monthly",
        quality: "ok",
        asOf: "2024-05-31",
        passStatus: "fail",
        macdDif: -0.2,
        macdSignal: 0.1,
        macdHistogram: -0.3,
        kdK: 40,
        kdD: 42,
      },
    });
    renderPanel("TW:2330");
    expect(await screen.findByTestId("macd-kd-indicators-panel")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("macd-kd-daily")).toHaveTextContent("1.2500");
      expect(screen.getByTestId("macd-kd-monthly")).toHaveTextContent("-0.2000");
    });
  });

  it("shows unavailable reason instead of zeros", async () => {
    vi.mocked(fetchTechnicalIndicators).mockResolvedValue({
      stockId: "TW:2330",
      calculationVersion: "macd12-26-9_kd9-3-3_v1",
      upstreamFetch: false,
      daily: {
        timeframe: "daily",
        quality: "insufficient_history",
        unavailableReason: "insufficient_history",
        macdDif: null,
        dif: null,
      },
      monthly: {
        timeframe: "monthly",
        quality: "insufficient_history",
        unavailableReason: "insufficient_history",
        macdDif: null,
        dif: null,
      },
    });
    renderPanel("TW:2330");
    expect(await screen.findByTestId("macd-kd-daily-unavailable")).toBeInTheDocument();
    expect(screen.queryByText("0.0000")).not.toBeInTheDocument();
  });

  it("renders stale as unavailable with last bar date", async () => {
    vi.mocked(fetchTechnicalIndicators).mockResolvedValue({
      stockId: "TW:2330",
      calculationVersion: "macd12-26-9_kd9-3-3_v1",
      upstreamFetch: false,
      daily: {
        timeframe: "daily",
        quality: "stale",
        unavailableReason: "latest_bar_before_last_completed_session",
        asOf: "2021-06-28",
        lastBarDate: "2021-06-28",
        macdDif: null,
        passStatus: null,
      },
      monthly: {
        timeframe: "monthly",
        quality: "source_incomplete",
        unavailableReason: "incomplete_closed_month",
        asOf: "2021-05-31",
        macdDif: null,
        passStatus: null,
      },
    });
    renderPanel("TW:2330");
    expect(await screen.findByTestId("macd-kd-daily-unavailable")).toHaveTextContent(
      /unavailable \(stale\)|不可用（已過期/i,
    );
    expect(screen.getByTestId("macd-kd-daily-unavailable")).toHaveTextContent("2021-06-28");
  });

  it("does not fetch for US stocks", () => {
    const { container } = renderPanel("US:AAPL");
    expect(container).toBeEmptyDOMElement();
    expect(fetchTechnicalIndicators).not.toHaveBeenCalled();
  });
});
