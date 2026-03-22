import type { Candle, ChartLayout, Quote, TimeRange } from "./quote";
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
  chartLayout: ChartLayout | null;
};

export type WatchlistResponse = {
  ids: string[];
};

/** Extended stats for stock detail (from quote + Yahoo chart or local candles). */
export type KeyStatistics = {
  open: number | null;
  previousClose: number | null;
  volume: number | null;
  lastPrice: number | null;
  dayLow: number | null;
  dayHigh: number | null;
  fiftyTwoWeekLow: number | null;
  fiftyTwoWeekHigh: number | null;
  fiftyTwoWeekLowDate: string | null;
  fiftyTwoWeekHighDate: string | null;
  avgVolume10d: number | null;
  marketCap: string | null;
  sharesOutstanding: string | null;
  beta: number | null;
  peTtm: number | null;
  epsTtm: number | null;
  dividendQuarterly: number | null;
  dividendYieldPercent: number | null;
  exDividendDate: string | null;
  dividendPayDate: string | null;
  nextEarningsDate: string | null;
  /** 0–100 position of last price within day low–high (server). */
  dayRangeMarkerPercent: number | null;
  /** 0–100 position of last price within 52-week low–high (server). */
  week52RangeMarkerPercent: number | null;
};

