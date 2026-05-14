import type {
  HistoryResponse,
  RankingResponse,
  RankingType,
  KeyStatistics,
  MarketIndex,
  MarketStatus,
  FxRate,
  NewsItem,
  ApiKeyStatus,
  AiInsightResponse,
  PortfolioAsset,
  PortfolioAssetCreate,
  PortfolioHistoryResponse,
  PortfolioAssetSummary,
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

export type ApiErrorKind =
  | "auth"
  | "permission"
  | "unavailable"
  | "validation"
  | "not_found"
  | "unknown";

export class ApiError extends Error {
  readonly detail: string | null;
  readonly kind: ApiErrorKind;
  readonly status: number | null;

  constructor(message: string, options: { status?: number | null; detail?: string | null; kind: ApiErrorKind }) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? null;
    this.detail = options.detail ?? null;
    this.kind = options.kind;
  }
}

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

function classifyApiErrorKind(status: number | null, detail: string | null, message: string): ApiErrorKind {
  const text = `${detail ?? ""} ${message}`.toLowerCase();

  if (status === 401 || text.includes("not authenticated") || text.includes("invalid username or password")) {
    return "auth";
  }
  if (status === 403 || text.includes("forbidden") || text.includes("permission")) {
    return "permission";
  }
  if (
    status === 502 ||
    status === 503 ||
    status === 504 ||
    text.includes("failed to fetch") ||
    text.includes("networkerror") ||
    text.includes("network request failed") ||
    text.includes("timeout") ||
    text.includes("temporarily unavailable") ||
    text.includes("provider unavailable")
  ) {
    return "unavailable";
  }
  if (status === 400) {
    return "validation";
  }
  if (status === 404) {
    return "not_found";
  }
  return "unknown";
}

/** FastAPI error bodies: `detail` as string, validation array, or object. */
async function failedRequestDetail(response: Response): Promise<string | null> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = payload.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (detail != null) {
      return JSON.stringify(detail);
    }
  } catch {
    /* ignore non-JSON body */
  }
  return null;
}

async function failedRequestError(response: Response): Promise<ApiError> {
  const detail = await failedRequestDetail(response);
  const message = detail ? `Request failed: ${response.status} - ${detail}` : `Request failed: ${response.status}`;
  return new ApiError(message, {
    status: response.status,
    detail,
    kind: classifyApiErrorKind(response.status, detail, message),
  });
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (error instanceof Error) {
    return new ApiError(error.message, {
      status: null,
      detail: error.message,
      kind: classifyApiErrorKind(null, error.message, error.message),
    });
  }
  return new ApiError("Unknown request failure", {
    status: null,
    detail: null,
    kind: "unknown",
  });
}

function mergeRequestInit(init?: RequestInit): RequestInit {
  return {
    ...defaultFetchInit,
    ...init,
    headers: {
      ...(defaultFetchInit.headers ?? {}),
      ...(init?.headers ?? {}),
    },
  };
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  try {
    const response = await fetch(apiUrl(path), mergeRequestInit(init));
    if (!response.ok) {
      throw await failedRequestError(response);
    }
    return response;
  } catch (error) {
    throw toApiError(error);
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init);
  return (await response.json()) as T;
}

export async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  await request(path, init);
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path);
  if (!response.ok) {
    throw await failedRequestError(response);
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

export function fetchRankings(
  market: "US" | "TW",
  rankingType: RankingType,
  limit = 50,
  twBoard?: "TWSE" | "TPEx",
) {
  const params = new URLSearchParams({
    market,
    type: rankingType,
    limit: String(limit),
  });
  if (market === "TW" && twBoard === "TPEx") {
    params.set("twBoard", "TPEx");
  }
  return fetchJson<RankingResponse>(`/rankings?${params.toString()}`);
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
  return requestJson<WatchlistResponse>("/watchlist/items", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ id: stockId }),
  });
}

export async function removeFromWatchlist(stockId: string) {
  return requestJson<WatchlistResponse>(`/watchlist/items/${encodeURIComponent(stockId)}`, {
    method: "DELETE",
  });
}

export function fetchPortfolioPositions() {
  return fetchJson<PortfolioPosition[]>("/portfolio/positions");
}

export function fetchPortfolioSummary() {
  return fetchJson<PortfolioSummary>("/portfolio/summary");
}

export function fetchPortfolioHoldingsHistory(range: TimeRange) {
  return fetchJson<PortfolioHistoryResponse>(
    `/portfolio/history/holdings?range=${encodeURIComponent(range)}`,
  );
}

export function fetchPortfolioCombinedAssetsHistory(range: TimeRange) {
  return fetchJson<PortfolioHistoryResponse>(
    `/portfolio/history/combined-assets?range=${encodeURIComponent(range)}`,
  );
}

export async function addPortfolioPosition(payload: PortfolioPositionCreate) {
  return requestJson<PortfolioPosition>("/portfolio/positions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function updatePortfolioPosition(positionId: number, payload: PortfolioPositionUpdate) {
  return requestJson<PortfolioPosition>(`/portfolio/positions/${positionId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function deletePortfolioPosition(positionId: number) {
  await requestVoid(`/portfolio/positions/${positionId}`, {
    method: "DELETE",
  });
}

export function fetchPortfolioAssets() {
  return fetchJson<PortfolioAsset[]>("/portfolio/assets");
}

export function fetchPortfolioAssetSummary() {
  return fetchJson<PortfolioAssetSummary>("/portfolio/assets/summary");
}

export async function addPortfolioAsset(payload: PortfolioAssetCreate) {
  return requestJson<PortfolioAsset>("/portfolio/assets", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function updatePortfolioAsset(assetId: number, payload: PortfolioAssetUpdate) {
  return requestJson<PortfolioAsset>(`/portfolio/assets/${assetId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function deletePortfolioAsset(assetId: number) {
  await requestVoid(`/portfolio/assets/${assetId}`, {
    method: "DELETE",
  });
}

export function fetchApiKeyStatus(provider: "openai" | "perplexity") {
  return fetchJson<ApiKeyStatus>(`/ai/keys/${provider}`);
}

export async function saveApiKey(provider: "openai" | "perplexity", apiKey: string) {
  return requestJson<ApiKeyStatus>(`/ai/keys/${provider}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ apiKey }),
  });
}

export function fetchAiInsight(stockId: string, provider: "openai" | "perplexity") {
  return fetchJson<AiInsightResponse>(
    `/ai/insights/${encodeURIComponent(stockId)}?provider=${provider}`,
  );
}
