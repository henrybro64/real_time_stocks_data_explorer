import type {
  AlertEvent,
  AlertRule,
  AlertRuleCreate,
  AlertRuleUpdate,
  HistoryResponse,
  RankingResponse,
  RankingsHistoryDatesResponse,
  RankingType,
  RankingUniverse,
  KeyStatistics,
  MarketIndex,
  MarketStatus,
  FxRate,
  CommodityQuote,
  NewsItem,
  AiProviderPreference,
  ApiKeyStatus,
  AiInsightResponse,
  PortfolioAsset,
  PortfolioAnalysisResponse,
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
  TaiwanInstitutionalInvestorsResponse,
  TaiwanMarginShortResponse,
  TaiwanOwnershipConcentrationResponse,
  TaiwanProfitabilityResponse,
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
  readonly retryAfterSeconds: number | null;

  constructor(
    message: string,
    options: {
      status?: number | null;
      detail?: string | null;
      kind: ApiErrorKind;
      retryAfterSeconds?: number | null;
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? null;
    this.detail = options.detail ?? null;
    this.kind = options.kind;
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
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
  if (
    status === 403 ||
    text.includes("forbidden") ||
    text.includes("permission") ||
    text.includes("denied access")
  ) {
    return "permission";
  }
  if (
    status === 429 ||
    status === 502 ||
    status === 503 ||
    status === 504 ||
    text.includes("failed to fetch") ||
    text.includes("networkerror") ||
    text.includes("network request failed") ||
    text.includes("timeout") ||
    text.includes("timed out") ||
    text.includes("temporarily unavailable") ||
    text.includes("provider unavailable") ||
    text.includes("rate-limited") ||
    text.includes("could not be interpreted")
  ) {
    return "unavailable";
  }
  if (status === 400 || status === 422) {
    return "validation";
  }
  if (status === 404) {
    return "not_found";
  }
  return "unknown";
}

/** Bounded React Query retries for stock-detail TW profitability (and similar). */
export const STOCK_DETAIL_QUERY_MAX_RETRIES = 2;

/**
 * Retry only transient upstream/network failures. Never retry validation,
 * auth, permission, or not-found — those will not fix themselves (#230).
 */
export function shouldRetryStockDetailQuery(failureCount: number, error: unknown): boolean {
  if (isAbortError(error)) {
    return false;
  }
  const apiError = error instanceof ApiError ? error : toApiError(error);
  if (
    apiError.kind === "validation" ||
    apiError.kind === "auth" ||
    apiError.kind === "permission" ||
    apiError.kind === "not_found" ||
    apiError.status === 400 ||
    apiError.status === 422 ||
    apiError.status === 401 ||
    apiError.status === 403 ||
    apiError.status === 404
  ) {
    return false;
  }
  if (
    apiError.status === 429 ||
    apiError.status === 502 ||
    apiError.status === 503 ||
    apiError.status === 504 ||
    apiError.kind === "unavailable"
  ) {
    return failureCount < STOCK_DETAIL_QUERY_MAX_RETRIES;
  }
  return false;
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

/** Parse HTTP Retry-After (delay-seconds or HTTP-date) into whole seconds. */
export function parseRetryAfterSeconds(header: string | null): number | null {
  if (header == null) {
    return null;
  }
  const trimmed = header.trim();
  if (trimmed === "") {
    return null;
  }
  const asSeconds = Number.parseInt(trimmed, 10);
  if (Number.isFinite(asSeconds) && String(asSeconds) === trimmed && asSeconds >= 0) {
    return asSeconds;
  }
  const asDateMs = Date.parse(trimmed);
  if (Number.isFinite(asDateMs)) {
    return Math.max(0, Math.ceil((asDateMs - Date.now()) / 1000));
  }
  return null;
}

async function failedRequestError(response: Response): Promise<ApiError> {
  const detail = await failedRequestDetail(response);
  const message = detail ? `Request failed: ${response.status} - ${detail}` : `Request failed: ${response.status}`;
  const retryAfterHeader =
    typeof response.headers?.get === "function"
      ? response.headers.get("Retry-After")
      : null;
  return new ApiError(message, {
    status: response.status,
    detail,
    kind: classifyApiErrorKind(response.status, detail, message),
    retryAfterSeconds: parseRetryAfterSeconds(retryAfterHeader),
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

function isAbortError(error: unknown): boolean {
  return error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError");
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  try {
    const response = await fetch(apiUrl(path), mergeRequestInit(init));
    if (!response.ok) {
      throw await failedRequestError(response);
    }
    return response;
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
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

export async function requestJsonWithBody<T>(
  path: string,
  method: "POST" | "PATCH" | "PUT",
  payload: unknown,
): Promise<T> {
  return requestJson<T>(path, {
    method,
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init);
  if (!response.ok) {
    throw await failedRequestError(response);
  }
  return (await response.json()) as T;
}

export const RANKINGS_REQUEST_TIMEOUT_MS = 20_000;
export const RANKINGS_REQUEST_TIMEOUT_DETAIL = "Rankings request timed out";

export function isRankingsRequestTimeoutError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.kind === "unavailable" &&
    error.detail === RANKINGS_REQUEST_TIMEOUT_DETAIL &&
    error.status == null
  );
}

export function shouldRetryRankingsQuery(failureCount: number, error: unknown): boolean {
  if (isRankingsRequestTimeoutError(error) || isAbortError(error)) {
    return false;
  }
  if (error instanceof ApiError) {
    // Definitive client/archive outcomes should not be re-polled.
    if (
      error.kind === "not_found" ||
      error.kind === "validation" ||
      error.status === 404 ||
      error.status === 422
    ) {
      return false;
    }
  }
  return failureCount < 3;
}

/** Bounded retries for Azure SQL AutoPause / cold-start on history dates (#177). */
export const HISTORY_DATES_MAX_RETRIES = 4;
export const HISTORY_DATES_RETRY_DELAY_MS = 8_000;

export function shouldRetryHistoryDatesQuery(
  failureCount: number,
  error: unknown,
): boolean {
  if (isAbortError(error)) {
    return false;
  }
  if (error instanceof ApiError) {
    // Only transient outages warrant AutoPause wake retries. SQL/app bugs (5xx
    // other than gateway/unavailable) must not burn the retry budget (#181).
    if (
      error.status === 503 ||
      error.status === 504 ||
      error.status === 502
    ) {
      return failureCount < HISTORY_DATES_MAX_RETRIES;
    }
    if (error.status == null && error.kind === "unavailable") {
      return failureCount < HISTORY_DATES_MAX_RETRIES;
    }
    return false;
  }
  return failureCount < HISTORY_DATES_MAX_RETRIES;
}

export function historyDatesRetryDelayMs(failureCount: number, error?: unknown): number {
  // Prefer server Retry-After (archive cooldown / wake hint) when present (#177).
  if (
    error instanceof ApiError
    && error.retryAfterSeconds != null
    && error.retryAfterSeconds > 0
  ) {
    return Math.max(error.retryAfterSeconds * 1000, HISTORY_DATES_RETRY_DELAY_MS);
  }
  // Align with backend cold-start cooldown / wake window.
  return HISTORY_DATES_RETRY_DELAY_MS * Math.max(1, failureCount);
}

async function fetchJsonWithTimeout<T>(
  path: string,
  timeoutMs: number,
  externalSignal?: AbortSignal,
  init?: RequestInit,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timeoutId = setTimeout(() => {
    if (externalSignal?.aborted) {
      return;
    }
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromCaller = () => {
    controller.abort();
  };
  if (externalSignal?.aborted) {
    controller.abort();
  } else {
    externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  try {
    return await fetchJson<T>(path, { ...init, signal: controller.signal });
  } catch (error) {
    if (externalSignal?.aborted) {
      if (isAbortError(error)) {
        throw error;
      }
      const cancelled = new Error("The user aborted a request.");
      cancelled.name = "AbortError";
      throw cancelled;
    }
    if (timedOut) {
      throw new ApiError(RANKINGS_REQUEST_TIMEOUT_DETAIL, {
        kind: "unavailable",
        detail: RANKINGS_REQUEST_TIMEOUT_DETAIL,
        status: null,
      });
    }
    if (isAbortError(error)) {
      throw error;
    }
    throw toApiError(error);
  } finally {
    clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", abortFromCaller);
  }
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

export function fetchWtiCrudeOilQuote() {
  return fetchJson<CommodityQuote>("/markets/commodities/wti-crude-oil");
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
  scope?: RankingUniverse,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    market,
    type: rankingType,
    limit: String(limit),
  });
  if (scope === "TWSE" || scope === "TPEx") {
    params.set("twBoard", scope);
    params.set("universe", scope);
  } else if (scope === "DJIA" || scope === "NASDAQ100") {
    params.set("universe", scope);
  }
  return fetchJsonWithTimeout<RankingResponse>(
    `/rankings?${params.toString()}`,
    RANKINGS_REQUEST_TIMEOUT_MS,
    signal,
  );
}

export function fetchRankingsHistoryDates(
  market: "US" | "TW",
  universe: RankingUniverse,
  rankingType: RankingType,
  limit = 5,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    market,
    universe,
    type: rankingType,
    limit: String(limit),
  });
  return fetchJsonWithTimeout<RankingsHistoryDatesResponse>(
    `/rankings/history/dates?${params.toString()}`,
    RANKINGS_REQUEST_TIMEOUT_MS,
    signal,
  );
}

export function fetchRankingsHistory(
  market: "US" | "TW",
  rankingType: RankingType,
  tradingDate: string,
  limit = 50,
  scope?: RankingUniverse,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    market,
    type: rankingType,
    date: tradingDate,
    limit: String(limit),
  });
  if (scope === "TWSE" || scope === "TPEx") {
    params.set("twBoard", scope);
    params.set("universe", scope);
  } else if (scope === "DJIA" || scope === "NASDAQ100") {
    params.set("universe", scope);
  }
  return fetchJsonWithTimeout<RankingResponse>(
    `/rankings/history?${params.toString()}`,
    RANKINGS_REQUEST_TIMEOUT_MS,
    signal,
  );
}

export function refreshRankings(
  market: "US" | "TW",
  rankingType: RankingType,
  scope: RankingUniverse,
  limit = 50,
  signal?: AbortSignal,
) {
  return fetchJsonWithTimeout<RankingResponse>(
    "/rankings/refresh",
    RANKINGS_REQUEST_TIMEOUT_MS,
    signal,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        market,
        rankingType,
        universe: scope,
        limit,
      }),
    },
  );
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

export function fetchTaiwanInstitutionalInvestors(stockId: string, days = 90) {
  return fetchJson<TaiwanInstitutionalInvestorsResponse>(
    `/stocks/${encodeURIComponent(stockId)}/institutional-investors?days=${days}`,
  );
}

export function fetchTaiwanMarginShort(stockId: string, days = 90) {
  return fetchJson<TaiwanMarginShortResponse>(
    `/stocks/${encodeURIComponent(stockId)}/margin-short?days=${days}`,
  );
}

export function fetchTaiwanOwnershipConcentration(
  stockId: string,
  range: "3M" | "6M" | "1Y" = "1Y",
) {
  return fetchJson<TaiwanOwnershipConcentrationResponse>(
    `/stocks/${encodeURIComponent(stockId)}/tw-ownership-concentration?range=${encodeURIComponent(range)}`,
  );
}

export function fetchTaiwanProfitability(stockId: string, quarters: 8 | 12 | 20 = 12) {
  return fetchJson<TaiwanProfitabilityResponse>(
    `/stocks/${encodeURIComponent(stockId)}/tw-profitability?quarters=${quarters}`,
  );
}

export function fetchTwStockInsights(stockId: string) {
  return fetchJson<TWStockInsights>(`/stocks/${encodeURIComponent(stockId)}/tw-insights`);
}

export function fetchUsStockInsights(stockId: string) {
  return fetchJson<USStockInsights>(`/stocks/${encodeURIComponent(stockId)}/us-insights`);
}

export function fetchStockAnalysis(
  stockId: string,
  options?: { refresh?: boolean; language?: "en" | "zh-TW" },
) {
  const params = new URLSearchParams();
  if (options?.refresh) {
    params.set("refresh", "true");
  }
  if (options?.language) {
    params.set("language", options.language);
  }
  const qs = params.toString();
  const suffix = qs ? `?${qs}` : "";
  return fetchJson<StockAnalysisResponse>(`/stocks/${encodeURIComponent(stockId)}/analysis${suffix}`);
}

export function fetchWatchlist() {
  return fetchJson<WatchlistResponse>("/watchlist");
}

export function fetchAlertRules() {
  return fetchJson<AlertRule[]>("/alerts/rules");
}

export function fetchAlertEvents(limit = 20) {
  return fetchJson<AlertEvent[]>(`/alerts/events?limit=${limit}`);
}

export async function createAlertRule(payload: AlertRuleCreate) {
  return requestJsonWithBody<AlertRule>("/alerts/rules", "POST", payload);
}

export async function updateAlertRule(ruleId: number, payload: AlertRuleUpdate) {
  return requestJsonWithBody<AlertRule>(`/alerts/rules/${ruleId}`, "PATCH", payload);
}

export async function deleteAlertRule(ruleId: number) {
  await requestVoid(`/alerts/rules/${ruleId}`, {
    method: "DELETE",
  });
}

export async function deleteAlertEvents() {
  await requestVoid("/alerts/events", {
    method: "DELETE",
  });
}

export async function addToWatchlist(stockId: string) {
  return requestJsonWithBody<WatchlistResponse>("/watchlist/items", "POST", { id: stockId });
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
  return requestJsonWithBody<PortfolioPosition>("/portfolio/positions", "POST", payload);
}

export async function updatePortfolioPosition(positionId: number, payload: PortfolioPositionUpdate) {
  return requestJsonWithBody<PortfolioPosition>(
    `/portfolio/positions/${positionId}`,
    "PATCH",
    payload,
  );
}

export async function deletePortfolioPosition(positionId: number) {
  await requestVoid(`/portfolio/positions/${positionId}`, {
    method: "DELETE",
  });
}

export async function analyzePortfolio() {
  return requestJson<PortfolioAnalysisResponse>("/portfolio/analysis", {
    method: "POST",
  });
}

export function fetchPortfolioAssets() {
  return fetchJson<PortfolioAsset[]>("/portfolio/assets");
}

export function fetchPortfolioAssetSummary() {
  return fetchJson<PortfolioAssetSummary>("/portfolio/assets/summary");
}

export async function addPortfolioAsset(payload: PortfolioAssetCreate) {
  return requestJsonWithBody<PortfolioAsset>("/portfolio/assets", "POST", payload);
}

export async function updatePortfolioAsset(assetId: number, payload: PortfolioAssetUpdate) {
  return requestJsonWithBody<PortfolioAsset>(`/portfolio/assets/${assetId}`, "PATCH", payload);
}

export async function deletePortfolioAsset(assetId: number) {
  await requestVoid(`/portfolio/assets/${assetId}`, {
    method: "DELETE",
  });
}

export function fetchAiProviderPreference() {
  return fetchJson<AiProviderPreference>("/ai/preferences");
}

export async function saveAiProviderPreference(provider: "openai" | "perplexity") {
  return requestJsonWithBody<AiProviderPreference>("/ai/preferences", "PUT", {
    preferredProvider: provider,
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
