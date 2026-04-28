import type {
  HistoryResponse,
  KeyStatistics,
  MarketIndex,
  MarketStatus,
  FxRate,
  NewsItem,
  ApiKeyStatus,
  AiInsightResponse,
  PortfolioAsset,
  PortfolioAssetCreate,
  PortfolioAssetUpdate,
  PortfolioPosition,
  PortfolioPositionCreate,
  PortfolioPositionUpdate,
  PortfolioSummary,
  QuoteResponse,
  StockAnalysisResponse,
  TWStockInsights,
  USStockInsights,
  WatchlistResponse,
} from "@shared/api";
import type { TimeRange } from "@shared/quote";
import type { Stock } from "@shared/stock";

/**
 * API environment (Next.js):
 *
 * - **Same-origin (recommended):** leave `NEXT_PUBLIC_API_BASE_URL` unset or empty.
 *   The browser calls `/api/...` on the current origin; Next.js rewrites proxy to the backend
 *   (see `BACKEND_PROXY_URL` in `next.config.mjs`).
 *
 * - **Direct backend (e.g. debugging):** set `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`
 *   and ensure `CORS_ORIGINS` on the backend includes your UI origin.
 */
const rawBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const API_BASE_URL =
  rawBase === undefined || rawBase === ""
    ? ""
    : rawBase.replace(/\/$/, "");

const API_PREFIX = (process.env.NEXT_PUBLIC_API_PREFIX ?? "/api").replace(/\/$/, "") || "/api";

function buildPath(path: string): string {
  if (!path.startsWith("/")) {
    throw new Error(`API path must start with '/': ${path}`);
  }
  return `${API_PREFIX}${path}`;
}

/** Full URL for HTTP(S) requests (relative when same-origin). */
export function apiUrl(path: string): string {
  const p = buildPath(path);
  if (API_BASE_URL === "") {
    return p;
  }
  return `${API_BASE_URL}${p}`;
}

const defaultFetchInit: RequestInit = {
  credentials: "include",
};

/** FastAPI error bodies: `detail` as string, validation array, or object. */
async function failedRequestMessage(response: Response): Promise<string> {
  let extra = "";
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = payload.detail;
    if (typeof detail === "string") {
      extra = ` - ${detail}`;
    } else if (detail != null) {
      extra = ` - ${JSON.stringify(detail)}`;
    }
  } catch {
    /* ignore non-JSON body */
  }
  return `Request failed: ${response.status}${extra}`;
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), defaultFetchInit);
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
  return (await response.json()) as T;
}

export function fetchMarkets() {
  return fetchJson<MarketStatus[]>("/markets");
}

export function fetchMarketIndices() {
  return fetchJson<MarketIndex[]>("/markets/indices");
}

export function fetchUsdTwdFxRate() {
  return fetchJson<FxRate>("/markets/fx/usd-twd");
}

export function searchStocks(query: string) {
  return fetchJson<Stock[]>(`/search?q=${encodeURIComponent(query)}`);
}

export function fetchQuotes(ids: string[]) {
  return fetchJson<QuoteResponse[]>(`/quotes?ids=${encodeURIComponent(ids.join(","))}`);
}

export function fetchQuote(stockId: string) {
  return fetchJson<QuoteResponse>(`/quotes/${encodeURIComponent(stockId)}`);
}

export function fetchHistory(stockId: string, range: TimeRange) {
  return fetchJson<HistoryResponse>(
    `/stocks/${encodeURIComponent(stockId)}/history?range=${range}`,
  );
}

export function fetchKeyStatistics(stockId: string) {
  return fetchJson<KeyStatistics>(`/stocks/${encodeURIComponent(stockId)}/key-statistics`);
}

export function fetchStockNews(stockId: string) {
  return fetchJson<NewsItem[]>(`/stocks/${encodeURIComponent(stockId)}/news`);
}

export function fetchTwStockInsights(stockId: string) {
  return fetchJson<TWStockInsights>(`/stocks/${encodeURIComponent(stockId)}/tw-insights`);
}

export function fetchUsStockInsights(stockId: string) {
  return fetchJson<USStockInsights>(`/stocks/${encodeURIComponent(stockId)}/us-insights`);
}

export function fetchStockAnalysis(stockId: string) {
  return fetchJson<StockAnalysisResponse>(`/stocks/${encodeURIComponent(stockId)}/analysis`);
}

export function fetchWatchlist() {
  return fetchJson<WatchlistResponse>("/watchlist");
}

export async function addToWatchlist(stockId: string) {
  const response = await fetch(apiUrl("/watchlist/items"), {
    ...defaultFetchInit,
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ id: stockId }),
  });

  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }

  return (await response.json()) as WatchlistResponse;
}

export async function removeFromWatchlist(stockId: string) {
  const response = await fetch(
    apiUrl(`/watchlist/items/${encodeURIComponent(stockId)}`),
    {
      ...defaultFetchInit,
      method: "DELETE",
    },
  );

  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }

  return (await response.json()) as WatchlistResponse;
}

export function fetchPortfolioPositions() {
  return fetchJson<PortfolioPosition[]>("/portfolio/positions");
}

export function fetchPortfolioSummary() {
  return fetchJson<PortfolioSummary>("/portfolio/summary");
}

export async function addPortfolioPosition(payload: PortfolioPositionCreate) {
  const response = await fetch(apiUrl("/portfolio/positions"), {
    ...defaultFetchInit,
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
  return (await response.json()) as PortfolioPosition;
}

export async function updatePortfolioPosition(positionId: number, payload: PortfolioPositionUpdate) {
  const response = await fetch(apiUrl(`/portfolio/positions/${positionId}`), {
    ...defaultFetchInit,
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
  return (await response.json()) as PortfolioPosition;
}

export async function deletePortfolioPosition(positionId: number) {
  const response = await fetch(apiUrl(`/portfolio/positions/${positionId}`), {
    ...defaultFetchInit,
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
}

export function fetchPortfolioAssets() {
  return fetchJson<PortfolioAsset[]>("/portfolio/assets");
}

export async function addPortfolioAsset(payload: PortfolioAssetCreate) {
  const response = await fetch(apiUrl("/portfolio/assets"), {
    ...defaultFetchInit,
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
  return (await response.json()) as PortfolioAsset;
}

export async function updatePortfolioAsset(assetId: number, payload: PortfolioAssetUpdate) {
  const response = await fetch(apiUrl(`/portfolio/assets/${assetId}`), {
    ...defaultFetchInit,
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
  return (await response.json()) as PortfolioAsset;
}

export async function deletePortfolioAsset(assetId: number) {
  const response = await fetch(apiUrl(`/portfolio/assets/${assetId}`), {
    ...defaultFetchInit,
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
}

export function fetchApiKeyStatus(provider: "openai" | "perplexity") {
  return fetchJson<ApiKeyStatus>(`/ai/keys/${provider}`);
}

export async function saveApiKey(provider: "openai" | "perplexity", apiKey: string) {
  const response = await fetch(apiUrl(`/ai/keys/${provider}`), {
    ...defaultFetchInit,
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ apiKey }),
  });
  if (!response.ok) {
    throw new Error(await failedRequestMessage(response));
  }
  return (await response.json()) as ApiKeyStatus;
}

export function fetchAiInsight(stockId: string, provider: "openai" | "perplexity") {
  return fetchJson<AiInsightResponse>(
    `/ai/insights/${encodeURIComponent(stockId)}?provider=${provider}`,
  );
}
