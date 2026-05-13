import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { RankingResponse } from "@shared/api";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError } from "../../services/api/client";
import { fetchRankings } from "../../services/api/client";
import { RankingsPage } from "./RankingsPage";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>("../../services/api/client");
  return {
    ...actual,
    fetchRankings: vi.fn(),
  };
});

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

function buildResponse(overrides: Partial<RankingResponse> = {}): RankingResponse {
  return {
    market: "TW",
    rankingType: "net_buy",
    items: [
      {
        rank: 1,
        stock: {
          id: "TW:2330",
          market: "TW",
          symbol: "2330",
          name: "台積電",
          exchange: "TWSE",
          currency: "TWD",
          timezone: "Asia/Taipei",
          nameEn: "TSMC",
          nameZhTw: "台積電",
        },
        lastPrice: 950,
        changePercent: 1.2,
        volume: 125000,
        volumeDeltaPercent: 8.4,
        netBuySellShares: null,
        timestamp: "2026-05-11T09:30:00+08:00",
      },
    ],
    timestamp: "2026-05-11T01:30:00Z",
    source: "TWSE T86 daily institutional data",
    sourceType: "free-public",
    isMarketOpen: true,
    dataFreshnessLabel: "TW daily institutional ranking sorted by highest 三大法人買賣超股數.",
    warningMessage: "台股三大法人買賣超為日資料，非盤中即時資料。",
    unsupportedReason: null,
    ...overrides,
  };
}

function renderRankingsPage(language: "en" | "zh-TW" = "zh-TW") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <RankingsPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("RankingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchRankings).mockResolvedValue(buildResponse());
  });

  it("renders ranking rows", async () => {
    renderRankingsPage();

    expect(await screen.findByRole("heading", { level: 1, name: "市場排行" })).toBeInTheDocument();
    expect(await screen.findByTestId("rankings-row-TW:2330")).toBeInTheDocument();
    expect(screen.getByTestId("rankings-realtime-tag")).toHaveTextContent("非即時");
    expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
      "此頁僅顯示台股法人日排行；本應用程式的免費資料來源無法提供美股全市場排行。",
    );
    expect(screen.getByTestId("rankings-warning-message")).toHaveTextContent(
      "台股三大法人買賣超為日資料，非盤中即時資料。",
    );
  });

  it("shows Taiwan-only controls and switches ranking tabs", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValue(
        buildResponse({
          rankingType: "net_sell",
          dataFreshnessLabel: "TW daily institutional ranking sorted by most negative 三大法人買賣超股數.",
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    expect(screen.queryByTestId("rankings-market-select")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rankings-tab-volume")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("rankings-tab-net_sell"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("TW", "net_sell", 50);
    });
  });

  it("renders loading state", () => {
    vi.mocked(fetchRankings).mockImplementation(() => new Promise(() => {}));

    renderRankingsPage();

    expect(screen.getByText("載入排行中")).toBeInTheDocument();
  });

  it("renders error state", async () => {
    vi.mocked(fetchRankings).mockRejectedValue(
      new ApiError("boom", { kind: "unknown", status: 500, detail: "boom" }),
    );

    renderRankingsPage();

    expect(await screen.findByText("載入失敗")).toBeInTheDocument();
  });

  it("renders TW daily-data warning for institutional rankings", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValue(
        buildResponse({
          rankingType: "net_sell",
          warningMessage: "台股三大法人買賣超為日資料，非盤中即時資料。",
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.click(screen.getByTestId("rankings-tab-net_sell"));

    expect(await screen.findByTestId("rankings-warning-message")).toHaveTextContent(
      "台股三大法人買賣超為日資料，非盤中即時資料。",
    );
  });
});
