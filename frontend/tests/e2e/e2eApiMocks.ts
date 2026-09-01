import type { Page, Route } from "@playwright/test";

import {
  E2E_TW_STOCK_SEARCH_RESULT,
  E2E_US_STOCK_SEARCH_RESULT,
  TPEX_STOCK_ID,
  TW_STOCK_ID,
  US_STOCK_ID,
} from "./helpers";

export const E2E_TW_QUOTE = {
  id: TW_STOCK_ID,
  lastPrice: 950,
  change: 5,
  changePercent: 0.53,
  previousClose: 945,
  open: 946,
  high: 952,
  low: 944,
  volume: 1_000_000,
  timestamp: "2026-05-11T09:00:00+08:00",
  isMarketOpen: true,
};

export const E2E_US_QUOTE = {
  id: US_STOCK_ID,
  lastPrice: 190.5,
  change: 2.25,
  changePercent: 1.2,
  previousClose: 188.25,
  open: 188.8,
  high: 191.2,
  low: 187.9,
  volume: 45_000_000,
  timestamp: "2026-05-10T16:00:00-04:00",
  isMarketOpen: false,
};

export const E2E_US_QUOTE_RESPONSE = {
  stock: E2E_US_STOCK_SEARCH_RESULT,
  quote: E2E_US_QUOTE,
};

export const E2E_TPEX_STOCK = {
  id: TPEX_STOCK_ID,
  market: "TW",
  symbol: "6488",
  name: "環球晶",
  nameEn: "GlobalWafers",
  nameZhTw: "環球晶",
  exchange: "TPEx",
  currency: "TWD",
  timezone: "Asia/Taipei",
} as const;

export const E2E_TPEX_QUOTE = {
  id: TPEX_STOCK_ID,
  lastPrice: 950,
  change: -65,
  changePercent: -6.4,
  previousClose: 1015,
  open: 1015,
  high: 1040,
  low: 936,
  volume: 4_452_000,
  timestamp: "2026-05-11T09:00:00+08:00",
  isMarketOpen: true,
};

export const E2E_TW_QUOTE_RESPONSE = {
  stock: E2E_TW_STOCK_SEARCH_RESULT,
  quote: E2E_TW_QUOTE,
};

export const E2E_TPEX_QUOTE_RESPONSE = {
  stock: E2E_TPEX_STOCK,
  quote: E2E_TPEX_QUOTE,
};

const E2E_MARKETS = [
  {
    market: "TW",
    label: "台灣市場",
    timezone: "Asia/Taipei",
    isOpen: true,
    localTime: "2026-05-11T10:00:00+08:00",
    withinHourBeforeOpen: false,
  },
  {
    market: "US",
    label: "U.S. Market",
    timezone: "America/New_York",
    isOpen: false,
    localTime: "2026-05-10T22:00:00-04:00",
    withinHourBeforeOpen: false,
  },
] as const;

const E2E_MARKET_INDICES = [
  {
    id: "TWSE:TAIEX",
    market: "TW",
    label: "Taiwan Stock Exchange",
    value: 21950.32,
    change: 85.44,
    changePercent: 0.39,
    timestamp: "2026-05-11T09:00:00+08:00",
    isMarketOpen: true,
    status: "ok",
    unavailableReason: null,
  },
  {
    id: "^DJI",
    market: "US",
    label: "DOW Jones",
    value: 39210.17,
    change: -102.12,
    changePercent: -0.26,
    timestamp: "2026-05-10T16:00:00-04:00",
    isMarketOpen: false,
    status: "ok",
    unavailableReason: null,
  },
  {
    id: "^NDQ",
    market: "US",
    label: "Nasdaq",
    value: 16980.4,
    change: 44.11,
    changePercent: 0.26,
    timestamp: "2026-05-10T16:00:00-04:00",
    isMarketOpen: false,
    status: "ok",
    unavailableReason: null,
  },
  {
    id: "^SPX",
    market: "US",
    label: "S&P 500",
    value: 5322.55,
    change: 9.87,
    changePercent: 0.19,
    timestamp: "2026-05-10T16:00:00-04:00",
    isMarketOpen: false,
    status: "ok",
    unavailableReason: null,
  },
] as const;

function buildRankingsResponse(url: URL) {
  const market = (url.searchParams.get("market") ?? "TW") as "TW" | "US";
  const rankingType = url.searchParams.get("type") ?? "volume";
  const twBoard = url.searchParams.get("twBoard") ?? "TWSE";
  const universe = url.searchParams.get("universe");

  if (market === "US" && (rankingType === "net_buy" || rankingType === "net_sell")) {
    return {
      market: "US",
      rankingType,
      items: [],
      timestamp: "2026-05-11T02:00:00Z",
      source: "Unsupported in this free-only app",
      sourceType: "unsupported",
      isMarketOpen: false,
      dataFreshnessLabel: "Unsupported in this free-only app.",
      warningMessage: null,
      unsupportedReason: "US institutional net-buy/net-sell is not available in this free-only app.",
      usRankingsScope: null,
    };
  }

  if (market === "US") {
    return {
      market: "US",
      rankingType,
      items: [
        {
          rank: 1,
          stock: {
            id: "US:AAPL",
            market: "US",
            symbol: "AAPL",
            name: "Apple Inc.",
            exchange: "NASDAQ",
            currency: "USD",
            timezone: "America/New_York",
            nameEn: "Apple Inc.",
          },
          lastPrice: 210.5,
          changePercent: 1.2,
          volume: 1000000,
          volumeDeltaPercent: null,
          netBuySellShares: null,
          timestamp: "2026-05-10T16:00:00-04:00",
        },
      ],
      timestamp: "2026-05-10T20:00:00Z",
      source: "Quote rankings from the app's free best-effort quote providers.",
      sourceType: "cache",
      isMarketOpen: false,
      dataFreshnessLabel: "Market is closed. Showing latest available free snapshot.",
      warningMessage: "Free unofficial quote source; data may be delayed or unavailable.",
      unsupportedReason: null,
      usRankingsScope: universe === "NASDAQ100" ? "nasdaq100" : "djia",
      universe: universe === "NASDAQ100" ? "NASDAQ100" : "DJIA",
    };
  }

  if (rankingType === "net_buy" || rankingType === "net_sell") {
    const isTpex = twBoard === "TPEx";
    return {
      market: "TW",
      rankingType,
      twBoard,
      items: [
        {
          rank: 1,
          stock: isTpex
            ? {
                ...E2E_TW_STOCK_SEARCH_RESULT,
                id: "TW:6488",
                symbol: "6488",
                name: "環球晶",
                nameEn: "GlobalWafers",
                exchange: "TPEx",
              }
            : E2E_TW_STOCK_SEARCH_RESULT,
          lastPrice: isTpex ? 480 : E2E_TW_QUOTE.lastPrice,
          changePercent: isTpex ? 1.4 : E2E_TW_QUOTE.changePercent,
          volume: isTpex ? 654321 : E2E_TW_QUOTE.volume,
          volumeDeltaPercent: null,
          netBuySellShares: rankingType === "net_buy" ? 500000 : -300000,
          timestamp: "2026-05-10T18:00:00+08:00",
        },
      ],
      timestamp: "2026-05-10T10:00:00Z",
      source: isTpex ? "TPEx institutional daily data" : "TWSE T86 daily institutional data",
      sourceType: "free-public",
      isMarketOpen: true,
      dataFreshnessLabel: isTpex
        ? "TPEx daily institutional ranking sorted by highest 三大法人買賣超股數."
        : "TW daily institutional ranking sorted by highest 三大法人買賣超股數.",
      warningMessage: isTpex
        ? "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
        : "台股三大法人買賣超為日資料，非盤中即時資料。",
      unsupportedReason: null,
      usRankingsScope: null,
    };
  }

  return {
    market: "TW",
    rankingType,
    twBoard,
    items: [
      {
        rank: 1,
        stock: E2E_TW_STOCK_SEARCH_RESULT,
        lastPrice: E2E_TW_QUOTE.lastPrice,
        changePercent: E2E_TW_QUOTE.changePercent,
        volume: E2E_TW_QUOTE.volume,
        volumeDeltaPercent: null,
        netBuySellShares: null,
        timestamp: E2E_TW_QUOTE.timestamp,
      },
    ],
    timestamp: "2026-05-11T01:00:00Z",
    source: "Quote rankings from the app's free best-effort quote providers.",
    sourceType: "cache",
    isMarketOpen: true,
    dataFreshnessLabel: "Showing the latest saved 30-minute free snapshot for this market window.",
    warningMessage: "Free unofficial quote source; data may be delayed or unavailable.",
    unsupportedReason: null,
    usRankingsScope: null,
  };
}

type PortfolioPositionRow = {
  id: number;
  stockId: string;
  quantity: number;
  averageCost: number;
  currentPrice: number | null;
  costBasis: number;
  currency: string;
  notes: string | null;
  stock: typeof E2E_TW_STOCK_SEARCH_RESULT;
  quote: typeof E2E_TW_QUOTE;
  marketValue: number;
  unrealizedPl: number;
  unrealizedPlPercent: number;
};

type AlertRuleRow = {
  id: number;
  stockId: string;
  stock: typeof E2E_TW_STOCK_SEARCH_RESULT;
  ruleType: "price_above" | "price_below";
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

type AlertEventRow = {
  id: number;
  alertRuleId: number | null;
  stockId: string;
  stock: typeof E2E_TW_STOCK_SEARCH_RESULT;
  ruleType: "price_above" | "price_below";
  thresholdValue: number;
  triggeredAt: string;
  observedPrice: number;
  quoteTimestamp: string | null;
  message: string;
};

type E2eAuthUser = {
  id: string;
  username: string;
};

type E2eAuthStatus = {
  authEnabled: boolean;
  loggedIn: boolean;
  providers: {
    local: boolean;
    google: boolean;
    github: boolean;
  };
  user: E2eAuthUser | null;
};

type E2eMockState = {
  authStatus: E2eAuthStatus;
  watchlistIds: string[];
  alertRules: AlertRuleRow[];
  alertEvents: AlertEventRow[];
  nextAlertRuleId: number;
  nextAlertEventId: number;
  portfolioPositions: PortfolioPositionRow[];
  nextPositionId: number;
};

const pagesWithApiMocks = new WeakSet<Page>();
const pageStates = new WeakMap<Page, E2eMockState>();

function defaultAuthStatus(): E2eAuthStatus {
  return {
    authEnabled: true,
    loggedIn: true,
    providers: { local: true, google: false, github: false },
    user: { id: "default-user", username: "default-user" },
  };
}

function getState(page: Page): E2eMockState {
  let state = pageStates.get(page);
  if (!state) {
    state = {
      authStatus: defaultAuthStatus(),
      watchlistIds: [],
      alertRules: [],
      alertEvents: [],
      nextAlertRuleId: 1,
      nextAlertEventId: 1,
      portfolioPositions: [],
      nextPositionId: 1,
    };
    pageStates.set(page, state);
  }
  return state;
}

export function setE2eAuthenticatedUser(page: Page, user: E2eAuthUser) {
  const state = getState(page);
  state.authStatus = {
    ...defaultAuthStatus(),
    user: { ...user },
  };
}

function apiPath(url: URL): string {
  const pathname = url.pathname;
  const apiIndex = pathname.indexOf("/api/");
  return apiIndex >= 0 ? pathname.slice(apiIndex) : pathname;
}

function decodeStockIdFromPath(path: string, prefix: string): string | null {
  if (!path.startsWith(prefix)) {
    return null;
  }
  return decodeURIComponent(path.slice(prefix.length));
}

function stockIdFromStocksPath(path: string): string | null {
  const match = path.match(/^\/api\/stocks\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function buildPortfolioPosition(
  state: E2eMockState,
  payload: {
    stockId: string;
    quantity: number;
    averageCost: number;
    currency: string;
    notes?: string | null;
  },
): PortfolioPositionRow {
  const marketValue = payload.quantity * E2E_TW_QUOTE.lastPrice;
  const costBasis = payload.quantity * payload.averageCost;
  const unrealizedPl = marketValue - costBasis;
  const unrealizedPlPercent =
    costBasis === 0 ? 0 : Number(((unrealizedPl / costBasis) * 100).toFixed(2));

  return {
    id: state.nextPositionId++,
    stockId: payload.stockId,
    quantity: payload.quantity,
    averageCost: payload.averageCost,
    currentPrice: E2E_TW_QUOTE.lastPrice,
    costBasis,
    currency: payload.currency,
    notes: payload.notes ?? null,
    stock: E2E_TW_STOCK_SEARCH_RESULT,
    quote: E2E_TW_QUOTE,
    marketValue,
    unrealizedPl,
    unrealizedPlPercent,
  };
}

function buildPortfolioSummary(positions: PortfolioPositionRow[]) {
  const usdTotal = positions
    .filter((position) => position.currency === "USD")
    .reduce((sum, position) => sum + position.marketValue, 0);
  const twdTotal = positions
    .filter((position) => position.currency === "TWD")
    .reduce((sum, position) => sum + position.marketValue, 0);
  const usdCostBasis = positions
    .filter((position) => position.currency === "USD")
    .reduce((sum, position) => sum + position.quantity * position.averageCost, 0);
  const twdCostBasis = positions
    .filter((position) => position.currency === "TWD")
    .reduce((sum, position) => sum + position.quantity * position.averageCost, 0);
  const combinedCostUsd = Number((usdCostBasis + twdCostBasis / 32).toFixed(2));
  const combinedCostTwd = Number((twdCostBasis + usdCostBasis * 32).toFixed(2));
  const combinedGainUsd = Number(
    ((usdTotal - usdCostBasis) + (twdTotal - twdCostBasis) / 32).toFixed(2),
  );
  const combinedGainTwd = Number(
    ((twdTotal - twdCostBasis) + (usdTotal - usdCostBasis) * 32).toFixed(2),
  );
  const combinedGainPercent =
    combinedCostUsd === 0 ? 0 : Number(((combinedGainUsd / combinedCostUsd) * 100).toFixed(2));

  return {
    totalUsd: usdTotal,
    totalTwd: twdTotal,
    combinedUsd: Number((usdTotal + twdTotal / 32).toFixed(2)),
    combinedTwd: Number((twdTotal + usdTotal * 32).toFixed(2)),
    totalCostUsd: usdCostBasis,
    totalCostTwd: twdCostBasis,
    combinedCostUsd,
    combinedCostTwd,
    combinedGainUsd,
    combinedGainTwd,
    combinedGainPercent,
    usdTwdRate: 32,
    fxTimestamp: "2026-05-11T01:00:00Z",
    positions: positions.map((position) => ({
      stockId: position.stockId,
      symbol: position.stock.symbol,
      currency: position.currency,
      quantity: position.quantity,
      lastPrice: position.quote.lastPrice,
      marketValue: position.marketValue,
      allocationPercent:
        twdTotal === 0 ? 0 : Number(((position.marketValue / twdTotal) * 100).toFixed(2)),
      timestamp: position.quote.timestamp,
    })),
    skippedStockIds: [],
  };
}

const E2E_TW_INSIGHTS = {
  revenue: {
    lastUpdated: "2026-05-11",
    latestRevenueK: 1_000,
    latestMomPercent: 1.2,
    latestYoyPercent: 3.4,
    points: [{ month: "2026-01", revenueK: 1_000, momPercent: 1.2, yoyPercent: 3.4 }],
  },
  peer: {
    sector: "Semiconductors",
    asOf: "2026-05-11",
    companyCount: 10,
    metrics: [],
    compareUrl: null,
    topMovers: [],
  },
  dividends: {
    avgCashYield2Y: 1.8,
    years: [],
  },
};

const E2E_TW_INSTITUTIONAL = {
  stockId: TW_STOCK_ID,
  symbol: "2330",
  market: "TWSE",
  latestDate: "2026-07-06",
  summary: {
    foreignNetLots: 12345,
    investmentTrustNetLots: 1200,
    dealerNetLots: -800,
    totalNetLots: 12745,
  },
  history: [
    {
      date: "2026-07-04",
      foreignNetLots: 500,
      investmentTrustNetLots: 100,
      dealerNetLots: -50,
      totalNetLots: 550,
    },
    {
      date: "2026-07-05",
      foreignNetLots: -200,
      investmentTrustNetLots: 300,
      dealerNetLots: 100,
      totalNetLots: 200,
    },
    {
      date: "2026-07-06",
      foreignNetLots: 12345,
      investmentTrustNetLots: 1200,
      dealerNetLots: -800,
      totalNetLots: 12745,
    },
  ],
  sharesPerLot: 1000,
  source: "TWSE T86 daily institutional data",
};

const E2E_TW_MARGIN_SHORT = {
  stockId: "TW:2330",
  symbol: "2330",
  market: "TWSE",
  latestDate: "2026-07-06",
  summary: {
    marginBalanceLots: 31339,
    shortMarginBalanceLots: 0,
    borrowedShortBalanceLots: 12345,
    borrowedShortBalanceChangeLots: 141,
    borrowedShortBalanceChangeAmount10k: 2609,
  },
  history: [
    {
      date: "2026-07-04",
      closePrice: 181,
      marginBalanceLots: 31000,
      shortMarginBalanceLots: 200,
      borrowedShortBalanceLots: 12100,
      borrowedShortBalanceChangeLots: -20,
      borrowedShortBalanceChangeAmount10k: -362,
    },
    {
      date: "2026-07-05",
      closePrice: 183,
      marginBalanceLots: 31220,
      shortMarginBalanceLots: 120,
      borrowedShortBalanceLots: 12204,
      borrowedShortBalanceChangeLots: 104,
      borrowedShortBalanceChangeAmount10k: 1903,
    },
    {
      date: "2026-07-06",
      closePrice: 185,
      marginBalanceLots: 31339,
      shortMarginBalanceLots: 0,
      borrowedShortBalanceLots: 12345,
      borrowedShortBalanceChangeLots: 141,
      borrowedShortBalanceChangeAmount10k: 2609,
    },
  ],
  sharesPerLot: 1000,
  source: "TWSE official margin and borrowed-short daily data",
};

function buildMovingAveragesForCandles(
  candles: Array<{ time: string; close: number }>,
  range: string,
) {
  if (range === "1D" || range === "1W") {
    return null;
  }
  return {
    sma10: candles.map((candle, index) => ({
      time: candle.time,
      value: Number((candle.close - 1.1).toFixed(2)),
      direction: index === 0 ? "up" : index % 2 === 0 ? "up" : "down",
    })),
    sma20: candles.map((candle, index) => ({
      time: candle.time,
      value: Number((candle.close - 2.2).toFixed(2)),
      direction: index === 0 ? "down" : index % 2 === 0 ? "down" : "up",
    })),
    sma60: candles.map((candle, index) => ({
      time: candle.time,
      value: Number((candle.close - 5.5).toFixed(2)),
      direction: index === 0 ? null : "flat",
    })),
  };
}

const E2E_US_HISTORY_RESPONSE = {
  stock: E2E_US_STOCK_SEARCH_RESULT,
  range: "1D",
  candles: [
    {
      time: "2026-05-10T10:00:00-04:00",
      open: 188,
      high: 189.5,
      low: 187.5,
      close: 189,
      volume: 10_000_000,
    },
    {
      time: "2026-05-10T11:00:00-04:00",
      open: 189,
      high: 191,
      low: 188.8,
      close: 190.5,
      volume: 12_000_000,
    },
  ],
  chartLayout: {
    minY: 187.5,
    maxY: 191,
    points: [
      { x: 0, y: 70 },
      { x: 100, y: 20 },
    ],
    pointsPolyline: "0,70 100,20",
    trendUp: true,
    firstClose: 189,
    lastClose: 190.5,
    yAxisTicks: [187.5, 189.25, 191],
    xAxisTickIndices: [0, 1],
  },
  movingAverages: null,
};

const E2E_HISTORY_RESPONSE = {
  stock: E2E_TW_STOCK_SEARCH_RESULT,
  range: "1D",
  candles: [
    {
      time: "2026-05-11T09:00:00+08:00",
      open: 940,
      high: 952,
      low: 938,
      close: 950,
      volume: 1_000_000,
    },
  ],
  chartLayout: {
    minY: 938,
    maxY: 952,
    points: [
      { x: 0, y: 100 },
      { x: 100, y: 0 },
    ],
    pointsPolyline: "0,100 100,0",
    trendUp: true,
    firstClose: 940,
    lastClose: 950,
    yAxisTicks: [938, 945, 952],
    xAxisTickIndices: [0],
  },
  movingAverages: null,
};

const E2E_TPEX_HISTORY_RESPONSE = {
  stock: E2E_TPEX_STOCK,
  range: "1D",
  candles: [
    {
      time: "2026-05-05T13:30:00+08:00",
      open: 980,
      high: 1000,
      low: 970,
      close: 990,
      volume: 3_000_000,
    },
    {
      time: "2026-05-06T13:30:00+08:00",
      open: 990,
      high: 1010,
      low: 985,
      close: 1005,
      volume: 3_200_000,
    },
    {
      time: "2026-05-11T13:30:00+08:00",
      open: 1015,
      high: 1040,
      low: 936,
      close: 950,
      volume: 4_452_000,
    },
  ],
  chartLayout: {
    minY: 936,
    maxY: 1040,
    points: [
      { x: 0, y: 70 },
      { x: 50, y: 40 },
      { x: 100, y: 80 },
    ],
    pointsPolyline: "0,70 50,40 100,80",
    trendUp: false,
    firstClose: 990,
    lastClose: 950,
    yAxisTicks: [936, 988, 1040],
    xAxisTickIndices: [0, 1, 2],
  },
  movingAverages: null,
};

function buildTpexHistoryForRange(range: string) {
  const candleCountByRange: Record<string, number> = {
    "1D": 1,
    "1W": 3,
    "1M": 5,
    "3M": 7,
    "6M": 9,
    "1Y": 11,
  };
  const count = candleCountByRange[range] ?? 3;
  const base = E2E_TPEX_HISTORY_RESPONSE.candles;
  const candles = Array.from({ length: count }, (_, index) => {
    const template = base[index % base.length];
    const day = String(5 + index).padStart(2, "0");
    return {
      ...template,
      time: `2026-05-${day}T13:30:00+08:00`,
      close: 900 + index * 10,
    };
  });
  const closes = candles.map((candle) => candle.close);
  const minY = Math.min(...candles.map((candle) => candle.low));
  const maxY = Math.max(...candles.map((candle) => candle.high));
  const movingAverages = buildMovingAveragesForCandles(candles, range);
  const smaValues =
    movingAverages == null
      ? []
      : [...movingAverages.sma10, ...movingAverages.sma20, ...movingAverages.sma60]
          .map((point) => point.value)
          .filter((value): value is number => value != null);
  const scaledMinY = smaValues.length > 0 ? Math.min(minY, ...smaValues) : minY;
  const scaledMaxY = smaValues.length > 0 ? Math.max(maxY, ...smaValues) : maxY;
  return {
    ...E2E_TPEX_HISTORY_RESPONSE,
    range,
    candles,
    movingAverages,
    chartLayout: {
      ...E2E_TPEX_HISTORY_RESPONSE.chartLayout,
      minY: scaledMinY,
      maxY: scaledMaxY,
      firstClose: closes.at(0),
      lastClose: closes.at(-1),
      points: candles.map((_, index) => ({
        x: candles.length === 1 ? 50 : (index / Math.max(candles.length - 1, 1)) * 100,
        y: 50,
      })),
      pointsPolyline: candles
        .map((_, index) => {
          const x = candles.length === 1 ? 50 : (index / Math.max(candles.length - 1, 1)) * 100;
          return `${x},50`;
        })
        .join(" "),
      xAxisTickIndices: candles.map((_, index) => index),
    },
  };
}

const E2E_PORTFOLIO_HISTORY = {
  range: "1M",
  metric: "market_value",
  usd: { points: [] },
  twd: {
    points: [{ timestamp: "2026-05-11T01:00:00Z", value: 950 }],
  },
};

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function conditionMet(ruleType: "price_above" | "price_below", thresholdValue: number) {
  return ruleType === "price_above"
    ? E2E_TW_QUOTE.lastPrice > thresholdValue
    : E2E_TW_QUOTE.lastPrice < thresholdValue;
}

function buildAlertMessage(ruleType: "price_above" | "price_below", thresholdValue: number) {
  const comparator = ruleType === "price_above" ? "rose above" : "fell below";
  return `${E2E_TW_STOCK_SEARCH_RESULT.symbol} ${comparator} ${thresholdValue.toFixed(2)} (observed ${E2E_TW_QUOTE.lastPrice.toFixed(2)}).`;
}

function evaluateEnabledAlertRule(
  state: E2eMockState,
  rule: AlertRuleRow,
): AlertRuleRow {
  const timestamp = E2E_TW_QUOTE.timestamp;
  const met = conditionMet(rule.ruleType, rule.thresholdValue);
  const evaluated: AlertRuleRow = {
    ...rule,
    lastObservedPrice: E2E_TW_QUOTE.lastPrice,
    lastObservedQuoteTimestamp: timestamp,
    lastEvaluatedAt: timestamp,
    updatedAt: timestamp,
  };

  if (met && !rule.lastConditionMet) {
    state.alertEvents.unshift({
      id: state.nextAlertEventId++,
      alertRuleId: rule.id,
      stockId: rule.stockId,
      stock: E2E_TW_STOCK_SEARCH_RESULT,
      ruleType: rule.ruleType,
      thresholdValue: rule.thresholdValue,
      triggeredAt: timestamp,
      observedPrice: E2E_TW_QUOTE.lastPrice,
      quoteTimestamp: timestamp,
      message: buildAlertMessage(rule.ruleType, rule.thresholdValue),
    });
    return {
      ...evaluated,
      lastConditionMet: true,
      lastTriggeredAt: timestamp,
    };
  }

  if (!met && rule.lastConditionMet) {
    return {
      ...evaluated,
      lastConditionMet: false,
    };
  }

  return evaluated;
}

function evaluateEnabledAlertRules(state: E2eMockState): void {
  state.alertRules = state.alertRules.map((rule) =>
    rule.enabled ? evaluateEnabledAlertRule(state, rule) : rule,
  );
}

function applyAlertRulePatch(
  state: E2eMockState,
  current: AlertRuleRow,
  payload: {
    stockId?: string;
    ruleType?: "price_above" | "price_below";
    thresholdValue?: number;
    enabled?: boolean;
  },
): AlertRuleRow {
  const stockId = payload.stockId ?? current.stockId;
  const ruleType = payload.ruleType ?? current.ruleType;
  const thresholdValue = payload.thresholdValue ?? current.thresholdValue;
  const enabled = payload.enabled ?? current.enabled;
  const definitionChanged =
    stockId !== current.stockId ||
    ruleType !== current.ruleType ||
    thresholdValue !== current.thresholdValue ||
    enabled !== current.enabled;
  const timestamp = E2E_TW_QUOTE.timestamp;

  let updated: AlertRuleRow = {
    ...current,
    stockId,
    stock: E2E_TW_STOCK_SEARCH_RESULT,
    ruleType,
    thresholdValue,
    enabled,
    lastConditionMet: definitionChanged ? false : current.lastConditionMet,
    updatedAt: timestamp,
  };

  if (!updated.enabled) {
    return updated;
  }

  return evaluateEnabledAlertRule(state, updated);
}

export async function installE2eApiMocks(page: Page): Promise<void> {
  if (pagesWithApiMocks.has(page)) {
    return;
  }
  pagesWithApiMocks.add(page);

  await page.route(/\/api\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = apiPath(url);
    const state = getState(page);

    if (path.startsWith("/api/search")) {
      const query = (url.searchParams.get("q") ?? "").trim();
      if (!query) {
        await fulfillJson(route, []);
        return;
      }
      const normalized = query.toLowerCase();
      const matchesTaiwanTsmc =
        normalized.includes("2330") ||
        normalized.includes("tsmc") ||
        normalized.includes("台積");
      await fulfillJson(route, matchesTaiwanTsmc ? [E2E_TW_STOCK_SEARCH_RESULT] : []);
      return;
    }

    if (path === "/api/auth/status" && request.method() === "GET") {
      await fulfillJson(route, state.authStatus);
      return;
    }

    if (path === "/api/markets" && request.method() === "GET") {
      await fulfillJson(route, E2E_MARKETS);
      return;
    }

    if (path === "/api/markets/indices" && request.method() === "GET") {
      await fulfillJson(route, E2E_MARKET_INDICES);
      return;
    }

    if (path === "/api/rankings/refresh" && request.method() === "POST") {
      const payload = (request.postDataJSON() ?? {}) as {
        market?: string;
        rankingType?: string;
        universe?: string;
      };
      const refreshUrl = new URL("https://e2e.test/api/rankings");
      refreshUrl.searchParams.set("market", payload.market ?? "TW");
      refreshUrl.searchParams.set("type", payload.rankingType ?? "volume");
      if (payload.universe === "TWSE" || payload.universe === "TPEx") {
        refreshUrl.searchParams.set("twBoard", payload.universe);
      }
      if (payload.universe) {
        refreshUrl.searchParams.set("universe", payload.universe);
      }
      await fulfillJson(route, {
        ...buildRankingsResponse(refreshUrl),
        sourceType: "free-unofficial",
      });
      return;
    }

    if (path === "/api/rankings/history/dates" && request.method() === "GET") {
      const market = (url.searchParams.get("market") ?? "TW") as "TW" | "US";
      const universe = (url.searchParams.get("universe") ??
        (market === "US" ? "DJIA" : "TWSE")) as "TWSE" | "TPEx" | "DJIA" | "NASDAQ100";
      await fulfillJson(route, {
        market,
        universe,
        dates: ["2026-08-20", "2026-08-19", "2026-08-18", "2026-08-17", "2026-08-14"],
      });
      return;
    }

    if (path === "/api/rankings/history" && request.method() === "GET") {
      const tradingDate = url.searchParams.get("date") ?? "2026-08-20";
      await fulfillJson(route, {
        ...buildRankingsResponse(url),
        tradingDate,
        captureScope: "end_of_day",
        isMarketOpen: false,
        dataFreshnessLabel: `Archived end-of-day rankings for ${tradingDate}.`,
      });
      return;
    }

    if (path.startsWith("/api/rankings") && request.method() === "GET") {
      await fulfillJson(route, buildRankingsResponse(url));
      return;
    }

    if (path === "/api/watchlist" && request.method() === "GET") {
      await fulfillJson(route, { ids: [...state.watchlistIds] });
      return;
    }

    if (path === "/api/alerts/rules" && request.method() === "GET") {
      evaluateEnabledAlertRules(state);
      await fulfillJson(route, [...state.alertRules]);
      return;
    }

    if (path === "/api/alerts/events" && request.method() === "GET") {
      evaluateEnabledAlertRules(state);
      const limit = Number(url.searchParams.get("limit") ?? state.alertEvents.length);
      await fulfillJson(route, state.alertEvents.slice(0, Number.isFinite(limit) ? limit : state.alertEvents.length));
      return;
    }

    if (path === "/api/alerts/events" && request.method() === "DELETE") {
      state.alertEvents = [];
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (path === "/api/alerts/rules" && request.method() === "POST") {
      const payload = request.postDataJSON() as {
        stockId?: string;
        ruleType?: "price_above" | "price_below";
        thresholdValue?: number;
        enabled?: boolean;
      };
      const ruleType = payload.ruleType ?? "price_above";
      const thresholdValue = payload.thresholdValue ?? E2E_TW_QUOTE.lastPrice;
      const enabled = payload.enabled ?? true;
      const isMet = enabled && conditionMet(ruleType, thresholdValue);
      const timestamp = E2E_TW_QUOTE.timestamp;
      const created: AlertRuleRow = {
        id: state.nextAlertRuleId++,
        stockId: payload.stockId ?? TW_STOCK_ID,
        stock: E2E_TW_STOCK_SEARCH_RESULT,
        ruleType,
        thresholdValue,
        enabled,
        lastConditionMet: isMet,
        lastObservedPrice: enabled ? E2E_TW_QUOTE.lastPrice : null,
        lastObservedQuoteTimestamp: enabled ? timestamp : null,
        lastEvaluatedAt: enabled ? timestamp : null,
        lastTriggeredAt: isMet ? timestamp : null,
        createdAt: timestamp,
        updatedAt: timestamp,
      };
      state.alertRules.push(created);
      if (isMet) {
        state.alertEvents.unshift({
          id: state.nextAlertEventId++,
          alertRuleId: created.id,
          stockId: created.stockId,
          stock: E2E_TW_STOCK_SEARCH_RESULT,
          ruleType,
          thresholdValue,
          triggeredAt: timestamp,
          observedPrice: E2E_TW_QUOTE.lastPrice,
          quoteTimestamp: timestamp,
          message: buildAlertMessage(ruleType, thresholdValue),
        });
      }
      await fulfillJson(route, created);
      return;
    }

    if (path === "/api/watchlist/items" && request.method() === "POST") {
      const payload = request.postDataJSON() as { id?: string };
      const stockId = payload.id ?? TW_STOCK_ID;
      if (!state.watchlistIds.includes(stockId)) {
        state.watchlistIds.push(stockId);
      }
      await fulfillJson(route, { ids: [...state.watchlistIds] });
      return;
    }

    const watchlistDeleteId = decodeStockIdFromPath(path, "/api/watchlist/items/");
    if (watchlistDeleteId && request.method() === "DELETE") {
      state.watchlistIds = state.watchlistIds.filter((id) => id !== watchlistDeleteId);
      await fulfillJson(route, { ids: [...state.watchlistIds] });
      return;
    }

    const alertRuleId = path.match(/^\/api\/alerts\/rules\/(\d+)$/);
    if (alertRuleId) {
      const ruleId = Number(alertRuleId[1]);
      const index = state.alertRules.findIndex((row) => row.id === ruleId);
      if (index < 0) {
        await fulfillJson(route, { detail: `Alert rule not found: ${ruleId}` }, 404);
        return;
      }

      if (request.method() === "PATCH") {
        const payload = request.postDataJSON() as {
          stockId?: string;
          ruleType?: "price_above" | "price_below";
          thresholdValue?: number;
          enabled?: boolean;
        };
        const updated = applyAlertRulePatch(state, state.alertRules[index], payload);
        state.alertRules[index] = updated;
        await fulfillJson(route, updated);
        return;
      }

      if (request.method() === "DELETE") {
        state.alertRules.splice(index, 1);
        await route.fulfill({ status: 204, body: "" });
        return;
      }
    }

    if (path === "/api/portfolio/positions" && request.method() === "GET") {
      await fulfillJson(route, [...state.portfolioPositions]);
      return;
    }

    if (path === "/api/portfolio/positions" && request.method() === "POST") {
      const payload = request.postDataJSON() as {
        stockId: string;
        quantity: number;
        averageCost: number;
        currency: string;
        notes?: string | null;
      };
      const existing = state.portfolioPositions.find((row) => row.stockId === payload.stockId);
      if (existing) {
        await fulfillJson(route, existing);
        return;
      }
      const created = buildPortfolioPosition(state, payload);
      state.portfolioPositions.push(created);
      await fulfillJson(route, created);
      return;
    }

    const portfolioPositionId = path.match(/^\/api\/portfolio\/positions\/(\d+)$/);
    if (portfolioPositionId) {
      const positionId = Number(portfolioPositionId[1]);
      const index = state.portfolioPositions.findIndex((row) => row.id === positionId);
      if (index < 0) {
        await fulfillJson(route, { detail: "Position not found." }, 404);
        return;
      }

      if (request.method() === "PATCH") {
        const payload = request.postDataJSON() as {
          quantity?: number;
          averageCost?: number;
          currency?: string;
          notes?: string | null;
        };
        const current = state.portfolioPositions[index];
        const quantity = payload.quantity ?? current.quantity;
        const averageCost = payload.averageCost ?? current.averageCost;
        const marketValue = quantity * E2E_TW_QUOTE.lastPrice;
        const costBasis = quantity * averageCost;
        const unrealizedPl = marketValue - costBasis;
        const unrealizedPlPercent =
          costBasis === 0 ? 0 : Number(((unrealizedPl / costBasis) * 100).toFixed(2));
        const updated: PortfolioPositionRow = {
          ...current,
          quantity,
          averageCost,
          currentPrice: E2E_TW_QUOTE.lastPrice,
          costBasis,
          currency: payload.currency ?? current.currency,
          notes: payload.notes ?? current.notes,
          marketValue,
          unrealizedPl,
          unrealizedPlPercent,
        };
        state.portfolioPositions[index] = updated;
        await fulfillJson(route, updated);
        return;
      }

      if (request.method() === "DELETE") {
        state.portfolioPositions.splice(index, 1);
        await route.fulfill({ status: 204, body: "" });
        return;
      }
    }

    if (path === "/api/portfolio/summary" && request.method() === "GET") {
      await fulfillJson(route, buildPortfolioSummary(state.portfolioPositions));
      return;
    }

    if (path.startsWith("/api/portfolio/history/") && request.method() === "GET") {
      await fulfillJson(route, E2E_PORTFOLIO_HISTORY);
      return;
    }

    if (path === "/api/markets/fx/usd-twd" && request.method() === "GET") {
      await fulfillJson(route, {
        pair: "USD/TWD",
        rate: 32,
        timestamp: "2026-05-11T01:00:00Z",
        change: 0.05,
        changePercent: 0.16,
        source: "Yahoo Finance",
        status: "ok",
      });
      return;
    }

    if (path === "/api/markets/commodities/wti-crude-oil" && request.method() === "GET") {
      await fulfillJson(route, {
        symbol: "CL=F",
        name: "WTI Crude Oil",
        price: 68.45,
        currency: "USD",
        unit: "barrel",
        change: -0.74,
        changePercent: -1.07,
        timestamp: "2026-05-11T01:05:00Z",
        source: "Yahoo Finance",
        isMarketOpen: false,
        status: "ok",
      });
      return;
    }

    if (path.startsWith("/api/quotes/") && request.method() === "GET") {
      const stockId = decodeStockIdFromPath(path, "/api/quotes/");
      if (stockId === TW_STOCK_ID) {
        await fulfillJson(route, E2E_TW_QUOTE_RESPONSE);
        return;
      }
      if (stockId === TPEX_STOCK_ID) {
        await fulfillJson(route, E2E_TPEX_QUOTE_RESPONSE);
        return;
      }
      if (stockId === US_STOCK_ID) {
        await fulfillJson(route, E2E_US_QUOTE_RESPONSE);
        return;
      }
    }

    if (path.startsWith("/api/quotes") && request.method() === "GET") {
      const ids = new Set(
        (url.searchParams.get("ids") ?? "")
          .split(",")
          .map((id) => id.trim())
          .filter(Boolean),
      );
      const items = [
        ...(ids.has(TW_STOCK_ID) ? [E2E_TW_QUOTE_RESPONSE] : []),
        ...(ids.has(TPEX_STOCK_ID) ? [E2E_TPEX_QUOTE_RESPONSE] : []),
        ...(ids.has(US_STOCK_ID) ? [E2E_US_QUOTE_RESPONSE] : []),
      ];
      await fulfillJson(route, items);
      return;
    }

    const stockId = stockIdFromStocksPath(path);
    if (stockId === TPEX_STOCK_ID) {
      if (path.endsWith("/history") && request.method() === "GET") {
        const range = url.searchParams.get("range") ?? "1D";
        await fulfillJson(route, buildTpexHistoryForRange(range));
        return;
      }
      if (path.endsWith("/key-statistics") && request.method() === "GET") {
        await fulfillJson(route, {
          open: E2E_TPEX_QUOTE.open,
          previousClose: E2E_TPEX_QUOTE.previousClose,
          volume: E2E_TPEX_QUOTE.volume,
          lastPrice: E2E_TPEX_QUOTE.lastPrice,
          dayLow: E2E_TPEX_QUOTE.low,
          dayHigh: E2E_TPEX_QUOTE.high,
          fiftyTwoWeekLow: 700,
          fiftyTwoWeekHigh: 1200,
          fiftyTwoWeekLowDate: "2025-05-11",
          fiftyTwoWeekHighDate: "2026-05-11",
          avgVolume10d: 3_000_000,
          marketCap: "400B",
          sharesOutstanding: "430M",
          beta: 1.2,
          peRatio: 18,
          peRatioType: "official_daily",
          peRatioSource: "TPEx official daily P/E",
          priceUsed: E2E_TPEX_QUOTE.lastPrice,
          priceAsOf: "2026-07-01",
          peTtm: 18,
          epsTtm: 52,
          epsAsOf: null,
          valuationAsOf: "2026-07-01",
          valuationWarning: null,
          dividendQuarterly: 2,
          dividendYieldPercent: 1.1,
          exDividendDate: "2026-06-01",
          dividendPayDate: "2026-07-01",
          nextEarningsDate: "2026-07-15",
          dayRangeMarkerPercent: 40,
          week52RangeMarkerPercent: 50,
        });
        return;
      }
      if (path.endsWith("/news") && request.method() === "GET") {
        await fulfillJson(route, []);
        return;
      }
      if (path.endsWith("/institutional-investors") && request.method() === "GET") {
        await fulfillJson(route, { ...E2E_TW_INSTITUTIONAL, stockId: TPEX_STOCK_ID, symbol: "6488", market: "TPEx" });
        return;
      }
      if (path.endsWith("/margin-short") && request.method() === "GET") {
        await fulfillJson(route, { ...E2E_TW_MARGIN_SHORT, stockId: TPEX_STOCK_ID, symbol: "6488", market: "TPEx" });
        return;
      }
      if (path.endsWith("/tw-insights") && request.method() === "GET") {
        await fulfillJson(route, E2E_TW_INSIGHTS);
        return;
      }
    }

    if (stockId === TW_STOCK_ID) {
      if (path.endsWith("/history") && request.method() === "GET") {
        const range = url.searchParams.get("range") ?? "1D";
        await fulfillJson(route, {
          ...E2E_HISTORY_RESPONSE,
          range,
          movingAverages: buildMovingAveragesForCandles(E2E_HISTORY_RESPONSE.candles, range),
        });
        return;
      }
      if (path.endsWith("/key-statistics") && request.method() === "GET") {
        await fulfillJson(route, {
          open: E2E_TW_QUOTE.open,
          previousClose: E2E_TW_QUOTE.previousClose,
          volume: E2E_TW_QUOTE.volume,
          lastPrice: E2E_TW_QUOTE.lastPrice,
          dayLow: E2E_TW_QUOTE.low,
          dayHigh: E2E_TW_QUOTE.high,
          fiftyTwoWeekLow: 500,
          fiftyTwoWeekHigh: 1100,
          fiftyTwoWeekLowDate: "2025-05-11",
          fiftyTwoWeekHighDate: "2026-05-11",
          avgVolume10d: 900_000,
          marketCap: "1000B",
          sharesOutstanding: "26B",
          beta: 1.1,
          peRatio: 20,
          peRatioType: "official_daily",
          peRatioSource: "TWSE official daily P/E",
          priceUsed: E2E_TW_QUOTE.lastPrice,
          priceAsOf: "2026-07-01",
          peTtm: 20,
          epsTtm: 40,
          epsAsOf: null,
          valuationAsOf: "2026-07-01",
          valuationWarning: null,
          dividendQuarterly: 3,
          dividendYieldPercent: 1.5,
          exDividendDate: "2026-06-01",
          dividendPayDate: "2026-07-01",
          nextEarningsDate: "2026-07-15",
          dayRangeMarkerPercent: 80,
          week52RangeMarkerPercent: 70,
        });
        return;
      }
      if (path.endsWith("/news") && request.method() === "GET") {
        await fulfillJson(route, []);
        return;
      }
      if (path.endsWith("/institutional-investors") && request.method() === "GET") {
        await fulfillJson(route, E2E_TW_INSTITUTIONAL);
        return;
      }
      if (path.endsWith("/margin-short") && request.method() === "GET") {
        await fulfillJson(route, E2E_TW_MARGIN_SHORT);
        return;
      }
      if (path.endsWith("/tw-insights") && request.method() === "GET") {
        await fulfillJson(route, E2E_TW_INSIGHTS);
        return;
      }
    }

    if (stockId === US_STOCK_ID) {
      if (path.endsWith("/history") && request.method() === "GET") {
        const range = url.searchParams.get("range") ?? "1D";
        await fulfillJson(route, {
          ...E2E_US_HISTORY_RESPONSE,
          range,
          movingAverages: buildMovingAveragesForCandles(E2E_US_HISTORY_RESPONSE.candles, range),
        });
        return;
      }
      if (path.endsWith("/key-statistics") && request.method() === "GET") {
        await fulfillJson(route, {
          open: E2E_US_QUOTE.open,
          previousClose: E2E_US_QUOTE.previousClose,
          volume: E2E_US_QUOTE.volume,
          lastPrice: E2E_US_QUOTE.lastPrice,
          dayLow: E2E_US_QUOTE.low,
          dayHigh: E2E_US_QUOTE.high,
          fiftyTwoWeekLow: 150,
          fiftyTwoWeekHigh: 220,
          fiftyTwoWeekLowDate: "2025-05-11",
          fiftyTwoWeekHighDate: "2026-05-11",
          avgVolume10d: 40_000_000,
          marketCap: "2900B",
          sharesOutstanding: "15B",
          beta: 1.2,
          peRatio: 28,
          peRatioType: "ttm",
          peRatioSource: "Yahoo Finance",
          priceUsed: E2E_US_QUOTE.lastPrice,
          priceAsOf: "2026-05-10",
          peTtm: 28,
          epsTtm: 6.8,
          epsAsOf: null,
          valuationAsOf: "2026-05-10",
          valuationWarning: null,
          dividendQuarterly: 0.24,
          dividendYieldPercent: 0.5,
          exDividendDate: "2026-05-01",
          dividendPayDate: "2026-05-15",
          nextEarningsDate: "2026-07-30",
          dayRangeMarkerPercent: 70,
          week52RangeMarkerPercent: 55,
        });
        return;
      }
      if (path.endsWith("/news") && request.method() === "GET") {
        await fulfillJson(route, []);
        return;
      }
      if (path.endsWith("/us-insights") && request.method() === "GET") {
        await fulfillJson(route, {
          peer: {
            asOf: "2026-05-10",
            sector: "Technology",
            industry: "Consumer Electronics",
            compareUrl: "https://example.com/peers",
            companySymbol: "AAPL",
            companyRankBy1m: 3,
            companyCount: 12,
            companyReturns: null,
            topMovers: [],
          },
        });
        return;
      }
    }

    await route.continue();
  });
}
