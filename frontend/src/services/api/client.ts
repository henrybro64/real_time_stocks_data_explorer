import type {
  HistoryResponse,
  KeyStatistics,
  MarketIndex,
  MarketStatus,
  NewsItem,
  QuoteResponse,
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
function apiUrl(path: string): string {
  const p = buildPath(path);
  if (API_BASE_URL === "") {
    return p;
  }
  return `${API_BASE_URL}${p}`;
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path));
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchMarkets() {
  return fetchJson<MarketStatus[]>("/markets");
}

export function fetchMarketIndices() {
  return fetchJson<MarketIndex[]>("/markets/indices");
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

export function fetchWatchlist() {
  return fetchJson<WatchlistResponse>("/watchlist");
}

export async function addToWatchlist(stockId: string) {
  const response = await fetch(apiUrl("/watchlist/items"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ id: stockId }),
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return (await response.json()) as WatchlistResponse;
}

export async function removeFromWatchlist(stockId: string) {
  const response = await fetch(
    apiUrl(`/watchlist/items/${encodeURIComponent(stockId)}`),
    {
      method: "DELETE",
    },
  );

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return (await response.json()) as WatchlistResponse;
}
