import type { Candle, Quote, TimeRange } from "./quote";
import type { MarketCode, Stock } from "./stock";

export type SearchResult = Stock;

export type MarketStatus = {
  market: MarketCode;
  label: string;
  timezone: string;
  isOpen: boolean;
  localTime: string;
};

export type MarketIndex = {
  id: string;
  market: MarketCode;
  label: string;
  value: number;
  change: number;
  changePercent: number;
  timestamp: string;
  isMarketOpen: boolean;
};

export type NewsItem = {
  title: string;
  url: string;
  source: string;
  publishedAt: string;
};

export type QuoteResponse = {
  stock: Stock;
  quote: Quote;
};

export type QuotesResponse = QuoteResponse[];

export type HistoryResponse = {
  stock: Stock;
  range: TimeRange;
  candles: Candle[];
};

export type WatchlistResponse = {
  ids: string[];
};

