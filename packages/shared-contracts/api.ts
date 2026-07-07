import type { Candle, ChartLayout, Quote, TimeRange } from "./quote";
import type { MarketCode, Stock } from "./stock";

export type SearchResult = Stock;

export type MarketStatus = {
  market: MarketCode;
  label: string;
  timezone: string;
  isOpen: boolean;
  localTime: string;
  /** True on a trading day in the hour before regular session open (local), while the venue is still closed. */
  withinHourBeforeOpen: boolean;
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

export type RankingType =
  | "volume"
  | "volume_up"
  | "volume_down"
  | "change_up"
  | "change_down"
  | "valuation_low_pe"
  | "net_buy"
  | "net_sell";

export type RankingSourceType =
  | "free-public"
  | "free-unofficial"
  | "cache"
  | "unsupported";

/** Present when `market` is US; drives the rankings scope banner copy. */
export type UsRankingsScope = "sp500" | "yahoo" | "quote";

export type RankingItem = {
  rank: number;
  stock: Stock;
  lastPrice: number | null;
  changePercent: number | null;
  volume: number | null;
  volumeDeltaPercent: number | null;
  netBuySellShares: number | null;
  peRatio?: number | null;
  peRatioType?: string | null;
  peRatioSource?: string | null;
  valuationAsOf?: string | null;
  valuationWarning?: string | null;
  timestamp: string;
};

export type RankingResponse = {
  market: MarketCode;
  rankingType: RankingType;
  twBoard?: "TWSE" | "TPEx" | null;
  items: RankingItem[];
  timestamp: string;
  source: string;
  sourceType: RankingSourceType;
  isMarketOpen: boolean;
  dataFreshnessLabel: string;
  warningMessage: string | null;
  unsupportedReason: string | null;
  usRankingsScope: UsRankingsScope | null;
};

export type HistoryResponse = {
  stock: Stock;
  range: TimeRange;
  candles: Candle[];
  chartLayout: ChartLayout | null;
};

export type WatchlistResponse = {
  ids: string[];
};

export type AlertRuleType = "price_above" | "price_below";

export type AlertRule = {
  id: number;
  stockId: string;
  stock: Stock | null;
  ruleType: AlertRuleType;
  thresholdValue: number;
  enabled: boolean;
  lastConditionMet: boolean;
  lastObservedPrice: number | null;
  lastObservedQuoteTimestamp: string | null;
  lastEvaluatedAt: string | null;
  lastTriggeredAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AlertEvent = {
  id: number;
  alertRuleId: number | null;
  stockId: string;
  stock: Stock | null;
  ruleType: AlertRuleType;
  thresholdValue: number;
  triggeredAt: string;
  observedPrice: number;
  quoteTimestamp: string | null;
  message: string;
};

export type AlertRuleCreate = {
  stockId: string;
  ruleType: AlertRuleType;
  thresholdValue: number;
  enabled?: boolean;
};

export type AlertRuleUpdate = {
  stockId?: string;
  ruleType?: AlertRuleType;
  thresholdValue?: number;
  enabled?: boolean;
};

export type PortfolioPosition = {
  id: number;
  stockId: string;
  quantity: number;
  averageCost: number;
  currentPrice: number | null;
  costBasis: number;
  currency: string;
  notes: string | null;
  stock: Stock;
  quote: Quote | null;
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
  lastPrice: number | null;
  marketValue: number;
  allocationPercent: number;
  timestamp: string | null;
};

export type PortfolioSummary = {
  totalUsd: number;
  totalTwd: number;
  combinedUsd: number;
  combinedTwd: number;
  totalCostUsd: number;
  totalCostTwd: number;
  combinedCostUsd: number;
  combinedCostTwd: number;
  combinedGainUsd: number;
  combinedGainTwd: number;
  combinedGainPercent: number;
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

export type PortfolioHistoryPoint = {
  timestamp: string;
  sourceWindow: string;
  fxRate: number;
  value: number;
};

export type PortfolioHistorySeries = {
  currency: "USD" | "TWD";
  points: PortfolioHistoryPoint[];
  chartLayout: ChartLayout | null;
};

export type PortfolioHistoryResponse = {
  range: TimeRange;
  metric: "holdings" | "combinedAssets" | string;
  usd: PortfolioHistorySeries;
  twd: PortfolioHistorySeries;
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

export type PortfolioAnalysisHolding = {
  stockId: string;
  symbol: string;
  name: string;
  market: string;
  exchange: string;
  currency: string;
  quantity: number;
  averageCost: number;
  lastPrice: number;
  costBasis: number;
  costBasisUsd: number;
  costBasisTwd: number;
  marketValue: number;
  marketValueUsd: number;
  marketValueTwd: number;
  unrealizedPl: number;
  unrealizedPlUsd: number;
  unrealizedPlTwd: number;
  unrealizedPlPercent: number;
  portfolioWeightPercent: number;
  notes: string | null;
};

export type PortfolioAnalysisSkippedHolding = {
  stockId: string;
  symbol: string | null;
  reason: string;
};

export type PortfolioAnalysisCurrencyExposure = {
  currency: string;
  marketValue: number;
  marketValueUsd: number;
  marketValueTwd: number;
  portfolioWeightPercent: number;
};

export type PortfolioConcentrationRisk = {
  level: "low" | "moderate" | "high" | string;
  topHoldingWeightPercent: number;
  top3WeightPercent: number;
  top5WeightPercent: number;
};

export type ComputedPortfolioAnalysis = {
  holdingsCount: number;
  analyzedHoldingsCount: number;
  skippedHoldingsCount: number;
  totalCostBasisUsd: number;
  totalCostBasisTwd: number;
  totalMarketValueUsd: number;
  totalMarketValueTwd: number;
  totalUnrealizedPlUsd: number;
  totalUnrealizedPlTwd: number;
  totalUnrealizedPlPercent: number;
  usdTwdRate: number;
  fxTimestamp: string;
  currencyExposure: PortfolioAnalysisCurrencyExposure[];
  largestHoldings: PortfolioAnalysisHolding[];
  holdings: PortfolioAnalysisHolding[];
  skippedHoldings: PortfolioAnalysisSkippedHolding[];
  concentrationRisk: PortfolioConcentrationRisk;
};

export type PortfolioAnalysisResponse = {
  computedSummary: ComputedPortfolioAnalysis;
  aiReport: string | null;
  provider: string | null;
  model: string | null;
  warnings: string[];
  disclaimer: string;
};

export type ApiKeyStatus = {
  provider: "openai" | "perplexity";
  configured: boolean;
  keyLast4: string | null;
};

export type AiProviderPreference = {
  preferredProvider: "openai" | "perplexity" | null;
};

export type AiInsightResponse = {
  provider: "openai" | "perplexity";
  stockId: string;
  content: string;
};

export type StockAnalysisCitation = {
  url: string;
  title: string | null;
  source: string | null;
};

export type StockAnalysisSearchResult = {
  title: string;
  url: string;
  source: string | null;
};

export type ComputedStockAnalysis = {
  ticker: string;
  companyName: string | null;
  currency: string | null;
  sector: string | null;
  industry: string | null;
  marketCap: number | null;
  trailingPe: number | null;
  forwardPe: number | null;
  currentPrice: number | null;
  return1mPercent: number | null;
  return3mPercent: number | null;
  return6mPercent: number | null;
  return1yPercent: number | null;
  sma50: number | null;
  sma200: number | null;
  priceVsSma50Percent: number | null;
  priceVsSma200Percent: number | null;
  rsi14: number | null;
  volatility30dAnnualized: number | null;
  maxDrawdown1yPercent: number | null;
  averageVolume30d: number | null;
};

export type StockAnalysisResponse = {
  stockId: string;
  ticker: string;
  computedData: ComputedStockAnalysis;
  aiProvider: string | null;
  aiModel: string | null;
  aiAnalysis: string | null;
  /** UTC ISO timestamp when the AI narrative was produced (after a refresh), if any. */
  analyzedAt: string | null;
  citations: StockAnalysisCitation[];
  searchResults: StockAnalysisSearchResult[];
  warnings: string[];
  errors: string[];
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
  peRatio: number | null;
  peRatioType: string | null;
  peRatioSource: string | null;
  priceUsed: number | null;
  priceAsOf: string | null;
  peTtm: number | null;
  epsTtm: number | null;
  epsAsOf: string | null;
  valuationAsOf: string | null;
  valuationWarning: string | null;
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
