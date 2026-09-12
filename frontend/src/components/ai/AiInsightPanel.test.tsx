import type { StockAnalysisResponse } from "@shared/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { fetchStockAnalysis } from "../../services/api/client";
import { AiInsightPanel } from "./AiInsightPanel";

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>(
    "../../services/api/client",
  );
  return {
    ...actual,
    fetchStockAnalysis: vi.fn(),
  };
});

function analysisResponse(overrides: Partial<StockAnalysisResponse> = {}): StockAnalysisResponse {
  return {
    stockId: "US:AAPL",
    ticker: "AAPL",
    language: "en",
    computedData: {
      ticker: "AAPL",
      companyName: "Apple Inc.",
      currency: "USD",
      sector: "Technology",
      industry: "Consumer Electronics",
      marketCap: 3_000_000_000_000,
      trailingPe: 29.4,
      forwardPe: 27.1,
      currentPrice: 210.5,
      return1mPercent: 4.2,
      return3mPercent: 8.7,
      return6mPercent: 12.4,
      return1yPercent: 22.1,
      sma50: 205.2,
      sma200: 188.4,
      priceVsSma50Percent: 2.58,
      priceVsSma200Percent: 11.73,
      rsi14: 61.4,
      volatility30dAnnualized: 24.6,
      maxDrawdown1yPercent: -14.8,
      averageVolume30d: 75_000_000,
    },
    aiProvider: "openai",
    aiModel: "gpt-4o-mini",
    aiAnalysis: "## Plain-language summary\nApple **may** be trending up.\n- Point one\n- Point two",
    analyzedAt: "2026-09-11T04:00:00Z",
    citations: [],
    searchResults: [],
    warnings: [],
    errors: [],
    ...overrides,
  };
}

function renderPanel(language: "en" | "zh-TW" = "en", stockId = "US:AAPL") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <LanguageProvider>
          <AiInsightPanel stockId={stockId} />
        </LanguageProvider>
      </QueryClientProvider>,
    ),
  };
}

describe("AiInsightPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("requests the analysis in the active UI language", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(analysisResponse());

    renderPanel("en");

    await waitFor(() => {
      expect(fetchStockAnalysis).toHaveBeenCalledWith("US:AAPL", { language: "en" });
    });
    expect(fetchStockAnalysis).toHaveBeenCalledTimes(1);
    expect(fetchStockAnalysis).not.toHaveBeenCalledWith("US:AAPL", { language: "zh-TW" });
  });

  it("requests the analysis in Traditional Chinese when the UI is zh-TW", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(
      analysisResponse({ language: "zh-TW", aiAnalysis: "## 白話摘要\n蘋果可能正在走強。" }),
    );

    renderPanel("zh-TW");

    await waitFor(() => {
      expect(fetchStockAnalysis).toHaveBeenCalledWith("US:AAPL", { language: "zh-TW" });
    });
    expect(await screen.findByText("白話摘要")).toBeInTheDocument();
  });

  it("never serves one language's narrative from the other's cache entry", async () => {
    vi.mocked(fetchStockAnalysis).mockImplementation(async (_stockId, options) =>
      options?.language === "en"
        ? analysisResponse({ language: "en", aiAnalysis: "English narrative" })
        : analysisResponse({ language: "zh-TW", aiAnalysis: "繁體中文分析" }),
    );

    const { queryClient } = renderPanel("en");

    await waitFor(() => {
      expect(queryClient.getQueryData(["stock-analysis", "US:AAPL", "en"])).toBeDefined();
    });

    const english = queryClient.getQueryData<StockAnalysisResponse>([
      "stock-analysis",
      "US:AAPL",
      "en",
    ]);
    expect(english?.aiAnalysis).toBe("English narrative");

    // Any entry stored under zh-TW must hold the zh-TW narrative, never the English one.
    const chinese = queryClient.getQueryData<StockAnalysisResponse>([
      "stock-analysis",
      "US:AAPL",
      "zh-TW",
    ]);
    if (chinese) {
      expect(chinese.aiAnalysis).toBe("繁體中文分析");
    }
  });

  it("sends refresh=true and the language when Refresh analysis is clicked", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(analysisResponse());

    renderPanel("en");
    await screen.findByText("Apple Inc.");

    await userEvent.click(screen.getByRole("button", { name: /refresh analysis/i }));

    await waitFor(() => {
      expect(fetchStockAnalysis).toHaveBeenCalledWith("US:AAPL", {
        refresh: true,
        language: "en",
      });
    });
  });

  it("renders narrative headings and bullets as structured markup", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(analysisResponse());

    renderPanel("en");

    expect(
      await screen.findByRole("heading", { name: "Plain-language summary" }),
    ).toBeInTheDocument();
    const bullets = await screen.findAllByRole("listitem");
    expect(bullets.map((item) => item.textContent)).toEqual(["Point one", "Point two"]);
  });

  it("strips inline markdown emphasis from the narrative", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(analysisResponse());

    renderPanel("en");

    expect(await screen.findByText("Apple may be trending up.")).toBeInTheDocument();
    expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument();
  });

  it("renders repeated bullet text without collapsing duplicates", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(
      analysisResponse({ aiAnalysis: "## Risks\n- Same point\n- Same point" }),
    );

    renderPanel("en");

    const bullets = await screen.findAllByRole("listitem");
    expect(bullets).toHaveLength(2);
  });

  it("never injects narrative text as raw HTML", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(
      analysisResponse({
        aiAnalysis: "## Summary\n<img src=x onerror=alert(1)>",
      }),
    );

    renderPanel("en");

    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("formats the analyzed-at timestamp instead of showing raw ISO text", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(analysisResponse());

    renderPanel("en");

    await screen.findByText("Apple Inc.");
    expect(screen.queryByText(/2026-09-11T04:00:00Z/)).not.toBeInTheDocument();
  });

  it("formats citation publication dates and opens links safely", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(
      analysisResponse({
        citations: [
          {
            url: "https://example.com/a",
            title: "Apple earnings beat",
            source: "Example",
            publishedAt: "2026-09-10T00:00:00Z",
          },
        ],
      }),
    );

    renderPanel("en");

    const link = await screen.findByRole("link", { name: "Apple earnings beat" });
    expect(link).toHaveAttribute("href", "https://example.com/a");
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.queryByText(/2026-09-10T00:00:00Z/)).not.toBeInTheDocument();
    expect(screen.getByText(/Example ·/)).toBeInTheDocument();
  });

  it("localizes known backend warnings", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(
      analysisResponse({
        warnings: ["Recent news context could not be loaded for this analysis."],
      }),
    );

    renderPanel("zh-TW");

    expect(await screen.findByText("無法載入近期新聞背景，本次分析僅依據市場資料。")).toBeInTheDocument();
  });

  it("shows unrecognized backend messages verbatim", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(
      analysisResponse({ errors: ["Something new went wrong."] }),
    );

    renderPanel("en");

    expect(await screen.findByText("Something new went wrong.")).toBeInTheDocument();
  });

  it("shows the no-cache hint when metrics exist but no narrative does", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(
      analysisResponse({ aiAnalysis: null, analyzedAt: null }),
    );

    renderPanel("en");

    expect(await screen.findByText(/No cached AI analysis is available yet/)).toBeInTheDocument();
    expect(screen.getByText("Apple Inc.")).toBeInTheDocument();
  });

  it("surfaces a load failure without crashing the panel", async () => {
    vi.mocked(fetchStockAnalysis).mockRejectedValue(new Error("boom"));

    renderPanel("en");

    expect(await screen.findByText(/Failed to load stock analysis/i)).toBeInTheDocument();
    // The panel keeps rendering its shell instead of blanking out.
    expect(screen.getByRole("heading", { name: "AI stock analysis" })).toBeInTheDocument();
  });

  it("always renders the disclaimer", async () => {
    vi.mocked(fetchStockAnalysis).mockResolvedValue(analysisResponse());

    renderPanel("en");

    expect(
      await screen.findByText(
        "This analysis is for informational purposes only and is not financial advice.",
      ),
    ).toBeInTheDocument();
  });
});
