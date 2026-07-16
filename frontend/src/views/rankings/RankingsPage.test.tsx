import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { MarketStatus, RankingResponse } from "@shared/api";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError } from "../../services/api/client";
import { fetchMarkets, fetchRankings } from "../../services/api/client";
import { RankingsPage } from "./RankingsPage";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>("../../services/api/client");
  return {
    ...actual,
    fetchRankings: vi.fn(),
    fetchMarkets: vi.fn(),
  };
});

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

function buildMarketStatus(overrides: Partial<MarketStatus> & Pick<MarketStatus, "market">): MarketStatus {
  const base: MarketStatus =
    overrides.market === "US"
      ? {
          market: "US",
          label: "U.S. Market",
          timezone: "America/New_York",
          isOpen: false,
          localTime: "2026-05-11T09:00:00-04:00",
          withinHourBeforeOpen: false,
        }
      : {
          market: "TW",
          label: "台灣市場",
          timezone: "Asia/Taipei",
          isOpen: false,
          localTime: "2026-05-11T09:00:00+08:00",
          withinHourBeforeOpen: false,
        };
  return { ...base, ...overrides };
}

function defaultMarketsPayload(): MarketStatus[] {
  return [buildMarketStatus({ market: "TW" }), buildMarketStatus({ market: "US" })];
}

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
    usRankingsScope: null,
    twBoard: "TWSE",
    ...overrides,
  };
}

function buildUsResponse(overrides: Partial<RankingResponse> = {}): RankingResponse {
  const nowIso = new Date().toISOString();

  return {
    market: "US",
    rankingType: "volume",
    items: [
      {
        rank: 1,
        stock: {
          id: "US:MSFT",
          market: "US",
          symbol: "MSFT",
          name: "Microsoft Corporation",
          exchange: "NASDAQ",
          currency: "USD",
          timezone: "America/New_York",
          nameEn: "Microsoft Corporation",
        },
        lastPrice: 425.5,
        changePercent: 0.8,
        volume: 12345678,
        volumeDeltaPercent: null,
        netBuySellShares: null,
        timestamp: nowIso,
      },
    ],
    timestamp: nowIso,
    source: "Quote rankings from the app's free best-effort quote providers.",
    sourceType: "free-unofficial",
    isMarketOpen: true,
    dataFreshnessLabel: "Market open; rankings use the latest quote snapshot.",
    warningMessage: "Free unofficial quote source; data may be delayed or unavailable.",
    unsupportedReason: null,
    usRankingsScope: "yahoo",
    ...overrides,
  };
}

function buildTwQuoteResponse(overrides: Partial<RankingResponse> = {}): RankingResponse {
  const nowIso = new Date().toISOString();

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
        lastPrice: 951,
        changePercent: 2.4,
        volume: 195000,
        volumeDeltaPercent: null,
        netBuySellShares: null,
        timestamp: nowIso,
      },
    ],
    timestamp: nowIso,
    source: "Quote rankings from the app's free best-effort quote providers.",
    sourceType: "free-unofficial",
    isMarketOpen: true,
    dataFreshnessLabel: "Market open; rankings use the latest quote snapshot.",
    warningMessage: "Free unofficial quote source; data may be delayed or unavailable.",
    unsupportedReason: null,
    usRankingsScope: null,
    twBoard: "TWSE",
    ...overrides,
  };
}

function buildTwValuationResponse(overrides: Partial<RankingResponse> = {}): RankingResponse {
  return {
    market: "TW",
    rankingType: "valuation_low_pe",
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
        lastPrice: 951,
        changePercent: 2.4,
        volume: 195000,
        volumeDeltaPercent: null,
        netBuySellShares: null,
        peRatio: 18.5,
        peRatioType: "official_daily",
        peRatioSource: "TWSE official daily P/E",
        valuationAsOf: "2026-07-03",
        valuationWarning: null,
        timestamp: "2026-07-03T13:20:00+08:00",
      },
    ],
    timestamp: "2026-07-03T05:20:00Z",
    source:
      "Valuation rankings use the highest-volume current quote candidates, then sort by P/E from the app's available valuation sources.",
    sourceType: "free-unofficial",
    isMarketOpen: true,
    dataFreshnessLabel:
      "Market open; valuation rankings use the latest quote snapshot filtered to the highest-volume candidates, then sort by the latest available P/E.",
    warningMessage: "Free unofficial quote source; data may be delayed or unavailable.",
    unsupportedReason: null,
    usRankingsScope: null,
    twBoard: "TWSE",
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

function volumeChangeColumnLabel(language: "en" | "zh-TW") {
  return language === "en" ? "Volume change %" : "量變化 %";
}

function netBuySellColumnLabel(language: "en" | "zh-TW") {
  return language === "en" ? "Net buy / sell" : "買賣超";
}

function expectVolumeChangeColumnAbsent(language: "en" | "zh-TW" = "zh-TW") {
  expect(
    screen.queryByRole("columnheader", { name: volumeChangeColumnLabel(language) }),
  ).not.toBeInTheDocument();
}

function expectNetBuySellColumnPresent(language: "en" | "zh-TW" = "zh-TW") {
  expect(screen.getByRole("columnheader", { name: netBuySellColumnLabel(language) })).toBeInTheDocument();
}

function expectNetBuySellColumnAbsent(language: "en" | "zh-TW" = "zh-TW") {
  expect(
    screen.queryByRole("columnheader", { name: netBuySellColumnLabel(language) }),
  ).not.toBeInTheDocument();
}

describe("RankingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pushMock.mockReset();
    vi.mocked(fetchMarkets).mockResolvedValue(defaultMarketsPayload());
    vi.mocked(fetchRankings).mockResolvedValue(buildResponse());
  });

  it("renders ranking rows", async () => {
    renderRankingsPage();

    expect(await screen.findByRole("heading", { level: 1, name: "市場排行" })).toBeInTheDocument();
    expect(await screen.findByTestId("rankings-row-TW:2330")).toBeInTheDocument();
    expect(await screen.findByRole("columnheader", { name: "板別" })).toBeInTheDocument();
    expect(screen.getByTestId("rankings-realtime-tag")).toHaveTextContent("非即時");
    expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
      "此處的台股法人排行僅含上市（TWSE）股票，資料來自 TWSE 法人日報表。",
    );
    expect(screen.getByTestId("rankings-warning-message")).toHaveTextContent(
      "台股三大法人買賣超為日資料，非盤中即時資料。",
    );
    expect(screen.getByTestId("rankings-source-type-tag")).toHaveTextContent(
      "來源類型: 免費公開",
    );
    expect(screen.getByTestId("rankings-snapshot-state-tag")).toHaveTextContent(
      "台股法人日資料",
    );
    expect(screen.getByTestId("rankings-snapshot-timestamp")).toHaveTextContent(
      "快照時間:",
    );
    expect(screen.getByTestId("rankings-change-percent-TW:2330")).toHaveClass(
      "app-price-up",
      "app-price-convention-taiwan",
    );
    expectNetBuySellColumnPresent();
    expectVolumeChangeColumnAbsent();
  });

  it("defaults market to US when only U.S. is within hour before open", async () => {
    vi.mocked(fetchMarkets).mockResolvedValue([
      buildMarketStatus({ market: "TW", withinHourBeforeOpen: false }),
      buildMarketStatus({ market: "US", withinHourBeforeOpen: true }),
    ]);
    vi.mocked(fetchRankings).mockResolvedValueOnce(buildResponse()).mockResolvedValue(buildUsResponse());

    renderRankingsPage();

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenCalledWith("US", "volume", 50, undefined);
    });
    expect(await screen.findByTestId("rankings-row-US:MSFT")).toBeInTheDocument();
    expect(screen.getByTestId("rankings-change-percent-US:MSFT")).toHaveClass(
      "app-price-up",
      "app-price-convention-us",
    );
  });

  it("switches markets and ranking tabs", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildUsResponse())
      .mockResolvedValueOnce(
        buildUsResponse({
          rankingType: "change_down",
          usRankingsScope: "yahoo",
          dataFreshnessLabel:
            "Market open; price-move rankings come from Yahoo Finance and refresh every few minutes.",
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.selectOptions(screen.getByLabelText("市場"), "US");

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "volume", 50, undefined);
    });
    expect(await screen.findByTestId("rankings-row-US:MSFT")).toBeInTheDocument();
    expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
      "美股此分頁採用 Yahoo Finance 預先排序的選股結果，涵蓋更廣的美股市場。",
    );
    expect(screen.getByTestId("rankings-realtime-tag")).toHaveTextContent("即時");

    await user.click(screen.getByTestId("rankings-tab-change_down"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "change_down", 50, undefined);
    });
    expectVolumeChangeColumnAbsent();
    expectNetBuySellColumnAbsent();
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
    expectNetBuySellColumnPresent();
    expectVolumeChangeColumnAbsent();
  });

  it("switches Taiwan tabs to quote-based rankings", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildTwQuoteResponse())
      .mockResolvedValueOnce(
        buildTwQuoteResponse({
          rankingType: "change_up",
          items: [
            {
              ...buildTwQuoteResponse().items[0],
              changePercent: 4.8,
            },
          ],
          dataFreshnessLabel:
            "Market open; price-move rankings use the latest quote snapshot.",
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.click(screen.getByTestId("rankings-tab-volume"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("TW", "volume", 50, "TWSE");
    });
    expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
      "此分頁的台股報價排行僅含上市（TWSE）且來自本應用程式設定的免費報價清單。",
    );
    expect(screen.getByTestId("rankings-warning-message")).toHaveTextContent(
      "免費非官方報價來源可能延遲，或暫時無法提供資料。",
    );
    expect(screen.getByTestId("rankings-realtime-tag")).toHaveTextContent("即時");
    expectNetBuySellColumnAbsent();
    expectVolumeChangeColumnAbsent();

    await user.click(screen.getByTestId("rankings-tab-change_up"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("TW", "change_up", 50, "TWSE");
    });
    await waitFor(() => {
      expect(screen.getByTestId("rankings-freshness-label")).toHaveTextContent(
        "市場開盤中；漲跌幅排行使用最新報價快照。",
      );
    });
    expectVolumeChangeColumnAbsent();
  });

  it("renders low P/E rankings with valuation columns", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildTwValuationResponse());

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.click(screen.getByTestId("rankings-tab-valuation_low_pe"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("TW", "valuation_low_pe", 50, "TWSE");
    });
    expect(await screen.findByRole("columnheader", { name: "本益比" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "本益比來源" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "估值日期" })).toBeInTheDocument();
    expectNetBuySellColumnAbsent();
    expectVolumeChangeColumnAbsent();
    expect(screen.getByText("18.50")).toBeInTheDocument();
    expect(screen.getByText("TWSE official daily P/E")).toBeInTheDocument();
    expect(screen.getByText("2026-07-03")).toBeInTheDocument();
  });

  it("renders U.S. low P/E rankings with valuation columns", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildUsResponse())
      .mockResolvedValueOnce(
        buildUsResponse({
          rankingType: "valuation_low_pe",
          items: [
            {
              ...buildUsResponse().items[0],
              peRatio: 22.4,
              peRatioType: "provider_ttm",
              peRatioSource: "Yahoo Finance trailing P/E",
              valuationAsOf: "2026-07-03",
              valuationWarning: null,
            },
          ],
          source:
            "Valuation rankings use the highest-volume current quote candidates, then sort by P/E from the app's available valuation sources.",
          dataFreshnessLabel:
            "Market open; valuation rankings use the latest quote snapshot filtered to the highest-volume candidates, then sort by the latest available P/E.",
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.selectOptions(screen.getByLabelText("市場"), "US");
    await user.click(screen.getByTestId("rankings-tab-valuation_low_pe"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "valuation_low_pe", 50, undefined);
    });
    expect(await screen.findByRole("columnheader", { name: "本益比" })).toBeInTheDocument();
    expect(screen.getByText("22.40")).toBeInTheDocument();
    expect(screen.getByText("Yahoo Finance trailing P/E")).toBeInTheDocument();
  });

  it("requests TPEx valuation rankings when 上櫃 board is selected", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(
        buildResponse({
          twBoard: "TPEx",
          items: [
            {
              ...buildResponse().items[0],
              stock: {
                ...buildResponse().items[0].stock,
                id: "TW:6488",
                symbol: "6488",
                name: "環球晶",
                exchange: "TPEx",
                nameEn: "GlobalWafers",
                nameZhTw: "環球晶",
              },
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        buildTwValuationResponse({
          twBoard: "TPEx",
          items: [
            {
              ...buildTwValuationResponse().items[0],
              stock: {
                ...buildTwValuationResponse().items[0].stock,
                id: "TW:6488",
                symbol: "6488",
                name: "環球晶",
                exchange: "TPEx",
                nameEn: "GlobalWafers",
                nameZhTw: "環球晶",
              },
              peRatio: 14.2,
              peRatioSource: "TPEx official daily P/E",
            },
          ],
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.click(screen.getByTestId("rankings-tw-board-tpex"));
    await user.click(screen.getByTestId("rankings-tab-valuation_low_pe"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("TW", "valuation_low_pe", 50, "TPEx");
    });
    expect(await screen.findByTestId("rankings-row-TW:6488")).toBeInTheDocument();
    expect(screen.getByText("TPEx official daily P/E")).toBeInTheDocument();
  });

  it("switches U.S. tabs to change-based rankings", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildUsResponse())
      .mockResolvedValueOnce(
        buildUsResponse({
          rankingType: "change_up",
          usRankingsScope: "sp500",
          items: [
            {
              ...buildUsResponse().items[0],
              changePercent: 5.3,
            },
          ],
          dataFreshnessLabel:
            "Market open; price-move rankings use the latest quote snapshot.",
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.selectOptions(screen.getByLabelText("市場"), "US");

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "volume", 50, undefined);
    });

    await user.click(screen.getByTestId("rankings-tab-change_up"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "change_up", 50, undefined);
    });
    expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
      "美股漲跌幅排行以標普五百成分股（內建名單快照）為範圍，使用 Yahoo Finance 批次報價欄位排序；美股成交量仍優先採用 Yahoo 全市場熱門成交選股（若可用）。",
    );
    await waitFor(() => {
      expect(screen.getByTestId("rankings-freshness-label")).toHaveTextContent(
        "市場開盤中；漲跌幅排行使用最新報價快照。",
      );
    });
  });

  it("shows TPEx institutional scope and ranking labels when 上櫃 is selected", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValue(
        buildResponse({
          twBoard: "TPEx",
          items: [
            {
              ...buildResponse().items[0],
              stock: {
                ...buildResponse().items[0].stock,
                id: "TW:6488",
                symbol: "6488",
                name: "環球晶",
                exchange: "TPEx",
                nameEn: "GlobalWafers",
                nameZhTw: "環球晶",
              },
              netBuySellShares: 500000,
            },
          ],
          source: "TPEx institutional daily data",
          sourceType: "free-public",
          dataFreshnessLabel: "TPEx daily institutional ranking sorted by highest 三大法人買賣超股數.",
          warningMessage: "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。",
          unsupportedReason: null,
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.click(screen.getByTestId("rankings-tw-board-tpex"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("TW", "net_buy", 50, "TPEx");
    });
    await waitFor(() => {
      expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
        "此處的台股法人排行僅含上櫃（TPEx）股票，資料來自 TPEx 法人日明細資料。",
      );
    });
    expect(screen.getByTestId("rankings-freshness-label")).toHaveTextContent(
      "上櫃（TPEx）法人日排行依三大法人買賣超股數由高到低排序。",
    );
    expect(screen.queryByTestId("rankings-unsupported-message")).not.toBeInTheDocument();
  });

  it("shows TPEx institutional unavailable copy when the source is unavailable", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValue(
        buildResponse({
          twBoard: "TPEx",
          items: [],
          source: "TPEx institutional daily data",
          sourceType: "unsupported",
          dataFreshnessLabel:
            "TPEx institutional daily ranking is unavailable from the free public source right now.",
          warningMessage: "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。",
          unsupportedReason:
            "TPEx institutional daily data is temporarily unavailable from the free public source.",
        }),
      );

    renderRankingsPage();

    await user.click(screen.getByRole("button", { name: "上櫃" }));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("TW", "net_buy", 50, "TPEx");
    });
    await waitFor(() => {
      expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
        "目前無法從免費公開來源取得上櫃（TPEx）法人排行。",
      );
    });
    expect(screen.getByTestId("rankings-freshness-label")).toHaveTextContent(
      "目前無法從免費公開來源取得上櫃（TPEx）法人日排行。",
    );
    expect(screen.getByTestId("rankings-unsupported-message")).toHaveTextContent(
      "目前無法從免費公開來源取得上櫃（TPEx）法人日資料。",
    );
  });

  it("shows explicit unsupported copy for U.S. institutional tabs", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildUsResponse())
      .mockResolvedValueOnce(
        buildUsResponse({
          rankingType: "net_buy",
          items: [],
          source: "Unsupported in this free-only app",
          sourceType: "unsupported",
          dataFreshnessLabel: "Unsupported in this free-only app.",
          warningMessage: null,
          unsupportedReason: "US institutional net-buy/net-sell is not available in this free-only app.",
          usRankingsScope: null,
        }),
      );

    renderRankingsPage();

    await screen.findByTestId("rankings-row-TW:2330");
    await user.selectOptions(screen.getByLabelText("市場"), "US");
    await user.click(screen.getByTestId("rankings-tab-net_buy"));

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "net_buy", 50, undefined);
    });
    expect(screen.getByTestId("rankings-scope-note")).toHaveTextContent(
      "此免費版本不提供美股法人買超／賣超。",
    );
    expect(screen.getByTestId("rankings-source-type-tag")).toHaveTextContent(
      "來源類型: 不支援",
    );
    expect(screen.getByTestId("rankings-snapshot-state-tag")).toHaveTextContent(
      "不支援",
    );
    expect(screen.getByTestId("rankings-unsupported-message")).toHaveTextContent(
      "此免費版本不提供美股法人買超／賣超。",
    );
  });

  it("hides volume change column in English while keeping institutional columns contextual", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRankings)
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildUsResponse());

    renderRankingsPage("en");

    expect(await screen.findByRole("heading", { level: 1, name: "Market rankings" })).toBeInTheDocument();
    await screen.findByTestId("rankings-row-TW:2330");
    expectNetBuySellColumnPresent("en");
    expectVolumeChangeColumnAbsent("en");

    await user.selectOptions(screen.getByLabelText("Market"), "US");

    await waitFor(() => {
      expect(fetchRankings).toHaveBeenLastCalledWith("US", "volume", 50, undefined);
    });
    await screen.findByTestId("rankings-row-US:MSFT");
    expectNetBuySellColumnAbsent("en");
    expectVolumeChangeColumnAbsent("en");
  });

  it("shows cached snapshot metadata when the API returns cache state", async () => {
    vi.mocked(fetchRankings).mockResolvedValue(
      buildTwQuoteResponse({
        sourceType: "cache",
        dataFreshnessLabel: "Market is closed. Showing latest available free snapshot.",
      }),
    );

    renderRankingsPage();

    expect(await screen.findByTestId("rankings-row-TW:2330")).toBeInTheDocument();
    expect(screen.getByTestId("rankings-freshness-label")).toHaveTextContent(
      "市場休市；顯示最新可得的免費快照。",
    );
    expect(screen.getByTestId("rankings-source-type-tag")).toHaveTextContent(
      "來源類型: 快取",
    );
    expect(screen.getByTestId("rankings-snapshot-state-tag")).toHaveTextContent(
      "最新快取快照",
    );
  });

  it("shows provider-failure fallback notice when the API returns fallback freshness", async () => {
    vi.mocked(fetchRankings).mockResolvedValue(
      buildTwQuoteResponse({
        sourceType: "cache",
        dataFreshnessLabel:
          "Free data source temporarily unavailable. Showing latest available snapshot if available.",
      }),
    );

    renderRankingsPage();

    expect(await screen.findByTestId("rankings-row-TW:2330")).toBeInTheDocument();
    expect(screen.getByTestId("rankings-freshness-label")).toHaveTextContent(
      "免費資料來源暫時無法使用；若有快照則顯示最新可得快照。",
    );
    expect(screen.getByTestId("rankings-source-type-tag")).toHaveTextContent(
      "來源類型: 快取",
    );
  });

  it("navigates to stock detail when a ranking row is activated", async () => {
    const user = userEvent.setup();
    renderRankingsPage();

    const row = await screen.findByTestId("rankings-row-TW:2330");
    const priceCell = row.querySelectorAll("td")[4];
    expect(priceCell).toBeTruthy();
    await user.click(priceCell!);

    expect(pushMock).toHaveBeenCalledWith("/stock/TW%3A2330");
  });

  it("exposes a real stock detail link inside ranking rows", async () => {
    renderRankingsPage();

    const link = await screen.findByRole("link", { name: "2330" });
    expect(link).toHaveAttribute("href", "/stock/TW%3A2330");
  });
});
