import type {
  HistoryResponse,
  MarketIndex,
  MarketStatus,
  NewsItem,
  QuoteResponse,
  WatchlistResponse,
} from "@shared/api";
import type { TimeRange } from "@shared/quote";
import type { Stock } from "@shared/stock";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const API_PREFIX = process.env.NEXT_PUBLIC_API_PREFIX ?? "/api";

function buildPath(path: string): string {
  if (!path.startsWith("/")) {
    throw new Error(`API path must start with '/': ${path}`);
  }
  return `${API_PREFIX}${path}`;
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${buildPath(path)}`);
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

export function fetchStockNews(stockId: string) {
  return fetchJson<NewsItem[]>(`/stocks/${encodeURIComponent(stockId)}/news`);
}

export function fetchWatchlist() {
  return fetchJson<WatchlistResponse>("/watchlist");
}

export async function addToWatchlist(stockId: string) {
  const response = await fetch(`${API_BASE_URL}${buildPath("/watchlist/items")}`, {
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
    `${API_BASE_URL}${buildPath(`/watchlist/items/${encodeURIComponent(stockId)}`)}`,
    {
      method: "DELETE",
    },
  );

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return (await response.json()) as WatchlistResponse;
}

