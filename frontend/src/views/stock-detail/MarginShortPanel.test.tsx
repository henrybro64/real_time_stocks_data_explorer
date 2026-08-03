import type { TaiwanMarginShortResponse } from "@shared/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ApiError, fetchTaiwanMarginShort } from "../../services/api/client";
import { MarginShortPanel } from "./MarginShortPanel";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchTaiwanMarginShort: vi.fn(),
  };
});

const samplePayload: TaiwanMarginShortResponse = {
  stockId: "TW:1303",
  symbol: "1303",
  market: "TWSE",
  latestDate: "2026-07-09",
  summary: {
    marginBalanceLots: 31339,
    shortMarginBalanceLots: 0,
    borrowedShortBalanceLots: 12345,
    borrowedShortBalanceChangeLots: 141,
    borrowedShortBalanceChangeAmount10k: 2609,
  },
  history: [
    {
      date: "2026-07-08",
      closePrice: 183,
      marginBalanceLots: 31220,
      shortMarginBalanceLots: 120,
      borrowedShortBalanceLots: 12204,
      borrowedShortBalanceChangeLots: 104,
      borrowedShortBalanceChangeAmount10k: 1903,
    },
    {
      date: "2026-07-09",
      closePrice: 185,
      marginBalanceLots: 31339,
      shortMarginBalanceLots: 0,
      borrowedShortBalanceLots: 12345,
      borrowedShortBalanceChangeLots: 141,
      borrowedShortBalanceChangeAmount10k: 2609,
    },
  ],
  sharesPerLot: 1000,
  source: "TWSE official margin and borrowed-short daily data",
};

function renderPanel(stockId = "TW:1303", language: "en" | "zh-TW" = "zh-TW") {
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
          <MarginShortPanel stockId={stockId} />
        </LanguageProvider>
      </QueryClientProvider>,
    ),
  };
}

describe("MarginShortPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("calls the margin-short API with stockId and days=90", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue(samplePayload);

    renderPanel();

    await waitFor(() => {
      expect(fetchTaiwanMarginShort).toHaveBeenCalledWith("TW:1303", 90);
    });
    expect(await screen.findByTestId("tw-margin-short-summary")).toBeInTheDocument();
  });

  it("renders summary rows with units and sign formatting from backend fields", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue(samplePayload);

    renderPanel();

    const summary = await screen.findByTestId("tw-margin-short-summary");
    expect(summary).toHaveTextContent("資券變化");
    expect(summary).toHaveTextContent("31,339 張");
    expect(summary).toHaveTextContent("0 張");
    expect(summary).toHaveTextContent("+141 張");
    expect(summary).toHaveTextContent("+2,609 萬");

    const values = summary.querySelectorAll("dd");
    expect(values[0]).toHaveClass("app-tw-market-neutral");
    expect(values[1]).toHaveClass("app-tw-market-neutral");
    expect(values[2]).toHaveClass("app-tw-market-neutral");
    expect(values[3]).toHaveClass("app-tw-market-positive");
    expect(values[4]).toHaveClass("app-tw-market-positive");

    expect(screen.getAllByTestId("tw-signal-line-chart")).toHaveLength(3);
    expect(screen.getAllByTestId("tw-institutional-chart")).toHaveLength(2);
    expect(screen.getByLabelText("融資餘額(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("融券餘額(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("借券賣出餘額(張)")).toBeInTheDocument();
    expect(screen.getByLabelText("借券賣出餘額增減張數")).toBeInTheDocument();
    expect(screen.getByLabelText("借券賣出餘額增減金額(萬)")).toBeInTheDocument();
  });

  it("formats negative borrowed-short changes with minus signs and negative tone", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      summary: {
        ...samplePayload.summary!,
        borrowedShortBalanceChangeLots: -141,
        borrowedShortBalanceChangeAmount10k: -2609,
      },
      history: [
        {
          ...samplePayload.history[0],
          borrowedShortBalanceChangeLots: -104,
          borrowedShortBalanceChangeAmount10k: -1903,
        },
        {
          ...samplePayload.history[1],
          borrowedShortBalanceChangeLots: -141,
          borrowedShortBalanceChangeAmount10k: -2609,
        },
      ],
    });

    renderPanel();

    const summary = await screen.findByTestId("tw-margin-short-summary");
    expect(summary).toHaveTextContent("-141 張");
    expect(summary).toHaveTextContent("-2,609 萬");

    const values = summary.querySelectorAll("dd");
    expect(values[3]).toHaveClass("app-tw-market-negative");
    expect(values[4]).toHaveClass("app-tw-market-negative");
  });

  it("renders an em dash when borrowed-short change amount is null in the summary", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      summary: {
        ...samplePayload.summary!,
        borrowedShortBalanceChangeAmount10k: null,
      },
    });

    renderPanel();

    const summary = await screen.findByTestId("tw-margin-short-summary");
    expect(summary).toHaveTextContent("+141 張");
    const amountRow = summary.querySelectorAll("dd")[4];
    expect(amountRow).toHaveTextContent("—");
    expect(amountRow).toHaveClass("app-tw-market-neutral");
  });

  it("renders partial margin-only rows when borrowed-short balances are null", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      market: "TPEx",
      stockId: "TW:6488",
      symbol: "6488",
      summary: {
        marginBalanceLots: 10200,
        shortMarginBalanceLots: 4,
        borrowedShortBalanceLots: null,
        borrowedShortBalanceChangeLots: null,
        borrowedShortBalanceChangeAmount10k: null,
      },
      history: [
        {
          date: "2026-07-09",
          closePrice: null,
          marginBalanceLots: 10200,
          shortMarginBalanceLots: 4,
          borrowedShortBalanceLots: null,
          borrowedShortBalanceChangeLots: null,
          borrowedShortBalanceChangeAmount10k: null,
        },
      ],
    });

    renderPanel("TW:6488");

    const summary = await screen.findByTestId("tw-margin-short-summary");
    expect(summary).toHaveTextContent("10,200 張");
    expect(summary).toHaveTextContent("4 張");
    const values = summary.querySelectorAll("dd");
    expect(values[2]).toHaveTextContent("—");
    expect(values[3]).toHaveTextContent("—");
    expect(values[4]).toHaveTextContent("—");
    expect(screen.getAllByTestId("tw-signal-line-chart")).toHaveLength(2);
    expect(screen.getByTestId("tw-signal-line-chart-empty")).toBeInTheDocument();
  });

  it("renders borrowed-only balance when margin fields and change metrics are null", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      summary: {
        marginBalanceLots: null,
        shortMarginBalanceLots: null,
        borrowedShortBalanceLots: 2100,
        borrowedShortBalanceChangeLots: null,
        borrowedShortBalanceChangeAmount10k: null,
      },
      history: [
        {
          date: "2026-07-09",
          closePrice: null,
          marginBalanceLots: null,
          shortMarginBalanceLots: null,
          borrowedShortBalanceLots: 2100,
          borrowedShortBalanceChangeLots: null,
          borrowedShortBalanceChangeAmount10k: null,
        },
      ],
    });

    renderPanel();

    const summary = await screen.findByTestId("tw-margin-short-summary");
    expect(summary).toHaveTextContent("2,100 張");
    const values = summary.querySelectorAll("dd");
    expect(values[0]).toHaveTextContent("—");
    expect(values[1]).toHaveTextContent("—");
    expect(values[2]).toHaveTextContent("2,100 張");
    expect(values[3]).toHaveTextContent("—");
    expect(values[4]).toHaveTextContent("—");
    expect(screen.getByLabelText("借券賣出餘額(張)")).toBeInTheDocument();
    expect(screen.getAllByTestId("tw-signal-line-chart")).toHaveLength(1);
  });

  it("renders English labels and units when language is en", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue(samplePayload);

    renderPanel("TW:1303", "en");

    const summary = await screen.findByTestId("tw-margin-short-summary");
    await waitFor(() => {
      expect(summary).toHaveTextContent("Margin / short changes");
    });
    expect(summary).toHaveTextContent("31,339 lots");
    expect(summary).toHaveTextContent("+141 lots");
    expect(summary).toHaveTextContent("+2,609 10k TWD");
    expect(screen.getByLabelText("Margin balance (lots)")).toBeInTheDocument();
  });

  it("keeps summary visible and shows empty chart copy when a series has no finite points", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      history: [
        {
          date: "2026-07-09",
          closePrice: 185,
          marginBalanceLots: 31339,
          shortMarginBalanceLots: 0,
          borrowedShortBalanceLots: 12345,
          borrowedShortBalanceChangeLots: 141,
          borrowedShortBalanceChangeAmount10k: null,
        },
      ],
    });

    renderPanel();

    expect(await screen.findByTestId("tw-margin-short-summary")).toHaveTextContent("+141 張");
    expect(screen.getByTestId("tw-institutional-chart-empty")).toHaveTextContent(
      "此區間沒有可繪製的資券資料。",
    );
  });

  it("keeps histogram timeline gaps when amount is null for a middle day", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      history: [
        {
          date: "2026-07-07",
          closePrice: 183,
          marginBalanceLots: 31200,
          shortMarginBalanceLots: 100,
          borrowedShortBalanceLots: 12100,
          borrowedShortBalanceChangeLots: 80,
          borrowedShortBalanceChangeAmount10k: 1464,
        },
        {
          date: "2026-07-08",
          closePrice: null,
          marginBalanceLots: 31220,
          shortMarginBalanceLots: 120,
          borrowedShortBalanceLots: 12204,
          borrowedShortBalanceChangeLots: 104,
          borrowedShortBalanceChangeAmount10k: null,
        },
        {
          date: "2026-07-09",
          closePrice: 185,
          marginBalanceLots: 31339,
          shortMarginBalanceLots: 0,
          borrowedShortBalanceLots: 12345,
          borrowedShortBalanceChangeLots: 141,
          borrowedShortBalanceChangeAmount10k: 2609,
        },
      ],
    });

    const { container } = renderPanel();

    await screen.findByTestId("tw-margin-short-summary");
    const amountChart = screen
      .getAllByTestId("tw-institutional-chart")
      .find((chart) => chart.querySelector('[aria-label="借券賣出餘額增減金額(萬)"]'));
    expect(amountChart).toBeTruthy();

    const bars = amountChart!.querySelectorAll("rect");
    expect(bars).toHaveLength(2);
    const firstX = Number(bars[0]?.getAttribute("x"));
    const secondX = Number(bars[1]?.getAttribute("x"));
    expect(secondX).toBeGreaterThan(50);
    expect(secondX).toBeGreaterThan(firstX + 20);
    expect(container.querySelectorAll('[data-testid="tw-signal-line-chart"]')).toHaveLength(3);
  });

  it("shows a loading state while the section is fetching", () => {
    vi.mocked(fetchTaiwanMarginShort).mockImplementation(
      () => new Promise(() => undefined),
    );

    renderPanel();

    expect(screen.getByText("載入資券變化中")).toBeInTheDocument();
  });

  it("shows empty copy when summary and history are empty", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      latestDate: null,
      summary: null,
      history: [],
      emptyReason: "no_data_for_stock",
    });

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("目前沒有可顯示的資券資料。")).toBeInTheDocument();
    });
  });

  it("shows not-published copy when official source has not released data yet", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue({
      ...samplePayload,
      latestDate: null,
      summary: null,
      history: [],
      emptyReason: "not_published",
    });

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("官方資券資料尚未公布，請稍後再試。")).toBeInTheDocument();
    });
  });

  it("shows empty copy for any 404 without depending on backend detail text", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockRejectedValue(
      new ApiError("not found", {
        kind: "not_found",
        status: 404,
        detail: "localized backend message",
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("目前沒有可顯示的資券資料。")).toBeInTheDocument();
    });
  });

  it("shows unavailable notice for 502 upstream failures", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockRejectedValue(
      new ApiError("Taiwan margin-short data is temporarily unavailable from official sources.", {
        kind: "unavailable",
        status: 502,
        detail: "Taiwan margin-short data is temporarily unavailable from official sources.",
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
      expect(screen.getByText(/資券變化目前無法從資料提供方取得/)).toBeInTheDocument();
    });
  });

  it("shows schema-failure notice when official response cannot be interpreted", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockRejectedValue(
      new ApiError("Taiwan margin-short official response could not be interpreted.", {
        kind: "unavailable",
        status: 502,
        detail: "Taiwan margin-short official response could not be interpreted.",
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("官方資料格式無法解析")).toBeInTheDocument();
      expect(screen.getByText(/回應格式無法判讀/)).toBeInTheDocument();
    });
  });

  it("shows rate-limit notice for 503 official source throttling", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockRejectedValue(
      new ApiError("Taiwan margin-short official source is rate-limited.", {
        kind: "unavailable",
        status: 503,
        detail: "Taiwan margin-short official source is rate-limited.",
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("官方資料來源流量受限")).toBeInTheDocument();
    });
  });

  it("shows official access-denied notice instead of app permission wording", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockRejectedValue(
      new ApiError("Taiwan margin-short official source denied access.", {
        kind: "permission",
        status: 502,
        detail: "Taiwan margin-short official source denied access.",
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("官方資料來源拒絕存取")).toBeInTheDocument();
      expect(screen.getByText(/不是本站登入問題/)).toBeInTheDocument();
    });
  });

  it("shows a compact stale notice when cached payload exists and refetch fails", async () => {
    vi.mocked(fetchTaiwanMarginShort).mockResolvedValue(samplePayload);
    const { queryClient } = renderPanel();

    await screen.findByTestId("tw-margin-short-summary");

    vi.mocked(fetchTaiwanMarginShort).mockRejectedValue(
      new ApiError("refresh failed", {
        kind: "unavailable",
        status: 502,
        detail: "refresh failed",
      }),
    );

    await queryClient.invalidateQueries({ queryKey: ["margin-short", "TW:1303", 90] });

    await waitFor(() => {
      expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    });
    expect(screen.getByTestId("tw-margin-short-summary")).toHaveTextContent("31,339 張");
  });
});
