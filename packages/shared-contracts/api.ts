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

export type FxRate = {
  pair: "USD/TWD" | string;
  rate: number;
  timestamp: string;
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

export type PortfolioPosition = {
  id: number;
  stockId: string;
  quantity: number;
  averageCost: number;
  currency: string;
  notes: string | null;
  stock: Stock;
  quote: Quote;
  marketValue: number;
  unrealizedPl: number;
  unrealizedPlPercent: number;
};

export type PortfolioPositionCreate = {
  stockId: string;
  quantity: number;
  averageCost: number;
  currency: string;
  notes?: string | null;
};

export type PortfolioPositionUpdate = {
  quantity?: number;
  averageCost?: number;
  currency?: string;
  notes?: string | null;
};

export type PortfolioSummaryPosition = {
  stockId: string;
  symbol: string;
  currency: string;
  quantity: number;
  lastPrice: number;
  marketValue: number;
  timestamp: string;
};

export type PortfolioSummary = {
  totalUsd: number;
  totalTwd: number;
  combinedUsd: number;
  combinedTwd: number;
  usdTwdRate: number;
  fxTimestamp: string;
  positions: PortfolioSummaryPosition[];
  skippedStockIds: string[];
};

export type PortfolioAssetSummary = {
  totalUsd: number;
  totalTwd: number;
  combinedUsd: number;
  combinedTwd: number;
  liquidTotalUsd: number;
  liquidTotalTwd: number;
  liquidCombinedUsd: number;
  liquidCombinedTwd: number;
  usdTwdRate: number;
  fxTimestamp: string;
  skippedStockIds: string[];
};

export type PortfolioAssetType = "cash" | "real_estate" | "other";

export type PortfolioAsset = {
  id: number;
  assetType: PortfolioAssetType | string;
  name: string;
  currency: string;
  value: number;
  notes: string | null;
};

export type PortfolioAssetCreate = {
  assetType: PortfolioAssetType | string;
  name: string;
  currency: string;
  value: number;
  notes?: string | null;
};

export type PortfolioAssetUpdate = {
  assetType?: PortfolioAssetType | string;
  name?: string;
  currency?: string;
  value?: number;
  notes?: string | null;
};

export type ApiKeyStatus = {
  provider: "openai" | "perplexity";
  configured: boolean;
  keyLast4: string | null;
};

export type AiInsightResponse = {
  provider: "openai" | "perplexity";
  stockId: string;
  content: string;
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

export type TWRevenuePoint = {
  month: string;
  revenueK: number;
  momPercent: number;
  yoyPercent: number;
};

export type TWRevenueInsight = {
  lastUpdated: string;
  latestRevenueK: number;
  latestMomPercent: number;
  latestYoyPercent: number;
  points: TWRevenuePoint[];
};

export type TWPeerMetric = {
  label: string;
  period: string;
  companyPercent: number;
  industryAveragePercent: number;
  rank: number;
  total: number;
};

export type TWPeerMover = {
  rank: number;
  name: string;
  symbol: string;
  change1mPercent: number;
  change3mPercent: number;
  change6mPercent: number;
  change1yPercent: number;
};

export type TWPeerInsight = {
  sector: string;
  asOf: string;
  companyCount: number;
  metrics: TWPeerMetric[];
  compareUrl: string | null;
  topMovers: TWPeerMover[];
};

export type TWDividendYear = {
  year: number;
  cashDividend: number;
  stockDividend: number;
  cashYieldPercent: number;
  exDividendDate: string | null;
  payDate: string | null;
};

export type TWDividendInsight = {
  avgCashYield2Y: number;
  years: TWDividendYear[];
};

export type TWStockInsights = {
  revenue: TWRevenueInsight;
  peer: TWPeerInsight;
  dividends: TWDividendInsight;
};

export type USPeerCompanyReturns = {
  change1mPercent: number;
  change3mPercent: number;
  change6mPercent: number;
  change1yPercent: number;
};

export type USPeerMover = {
  rank: number;
  symbol: string;
  change1mPercent: number;
  change3mPercent: number;
  change6mPercent: number;
  change1yPercent: number;
};

export type USPeerInsight = {
  asOf: string;
  sector: string;
  industry: string;
  compareUrl: string;
  companySymbol: string;
  companyRankBy1m: number | null;
  companyCount: number;
  companyReturns: USPeerCompanyReturns | null;
  topMovers: USPeerMover[];
};

export type USStockInsights = {
  peer: USPeerInsight;
};
