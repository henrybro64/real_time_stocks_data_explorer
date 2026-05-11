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
    rankingType: "volume",
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
    source: "Quote rankings from the app's free best-effort quote providers.",
    sourceType: "free-unofficial",
    isMarketOpen: true,
    dataFreshnessLabel: "Market open; rankings use the latest quote snapshot.",
    warningMessage: "Free unofficial quote source; data may be delayed or unavailable.",
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
    expect(screen.getByTestId("rankings-warning-message")).toHaveTextContent(
      "免費非官方報價來源，資料可能延遲或暫時無法取得。",
    );
  });

  it("switches market and ranking tabs", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValue(buildResponse({ market: "US", rankingType: "net_buy", items: [] }));

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.selectOptions(screen.getByTestId("rankings-market-select"), "US");
    await user.click(screen.getByTestId("rankings-tab-net_buy"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "net_buy", 50);
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

  it("renders unsupported US net-buy messaging", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValue(
        buildResponse({
          market: "US",
          rankingType: "net_buy",
          items: [],
          source: "Unsupported in Phase 1 free-only rankings",
          sourceType: "unsupported",
          warningMessage: null,
          unsupportedReason: "US institutional net-buy data is not available from free-only sources in this app.",
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.selectOptions(screen.getByTestId("rankings-market-select"), "US");
    await user.click(screen.getByTestId("rankings-tab-net_buy"));

    expect(await screen.findByTestId("rankings-unsupported-message")).toHaveTextContent(
      "此應用程式的免費資料來源無法提供美股法人買超排行。",
    );
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
