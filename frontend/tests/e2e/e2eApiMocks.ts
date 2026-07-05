import type { Page, Route } from "@playwright/test";

import { E2E_TW_STOCK_SEARCH_RESULT, TW_STOCK_ID } from "./helpers";

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

export const E2E_TW_QUOTE_RESPONSE = {
  stock: E2E_TW_STOCK_SEARCH_RESULT,
  quote: E2E_TW_QUOTE,
};

type PortfolioPositionRow = {
  id: number;
  stockId: string;
  quantity: number;
  averageCost: number;
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

type E2eMockState = {
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

function getState(page: Page): E2eMockState {
  let state = pageStates.get(page);
  if (!state) {
    state = {
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
  const twdTotal = positions
    .filter((position) => position.currency === "TWD")
    .reduce((sum, position) => sum + position.marketValue, 0);
  const twdCostBasis = positions
    .filter((position) => position.currency === "TWD")
    .reduce((sum, position) => sum + position.quantity * position.averageCost, 0);
  const combinedGainTwd = Number((twdTotal - twdCostBasis).toFixed(2));
  const combinedGainUsd = Number((combinedGainTwd / 32).toFixed(2));
  const combinedGainPercent =
    twdCostBasis === 0 ? 0 : Number((((twdTotal - twdCostBasis) / twdCostBasis) * 100).toFixed(2));

  return {
    totalUsd: 0,
    totalTwd: twdTotal,
    combinedUsd: twdTotal / 32,
    combinedTwd: twdTotal,
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
  },
};

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
      await fulfillJson(route, {
        authEnabled: true,
        loggedIn: true,
        providers: { local: true, google: false, github: false },
        user: { id: "default-user", username: "default-user" },
      });
      return;
    }

    if (path === "/api/watchlist" && request.method() === "GET") {
      await fulfillJson(route, { ids: [...state.watchlistIds] });
      return;
    }

    if (path === "/api/alerts/rules" && request.method() === "GET") {
      await fulfillJson(route, [...state.alertRules]);
      return;
    }

    if (path === "/api/alerts/events" && request.method() === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? state.alertEvents.length);
      await fulfillJson(route, state.alertEvents.slice(0, Number.isFinite(limit) ? limit : state.alertEvents.length));
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
      });
      return;
    }

    if (path.startsWith("/api/quotes/") && request.method() === "GET") {
      const stockId = decodeStockIdFromPath(path, "/api/quotes/");
      if (stockId === TW_STOCK_ID) {
        await fulfillJson(route, E2E_TW_QUOTE_RESPONSE);
        return;
      }
    }

    if (path.startsWith("/api/quotes") && request.method() === "GET") {
      const ids = (url.searchParams.get("ids") ?? "")
        .split(",")
        .map((id) => id.trim())
        .filter(Boolean);
      const items = ids.includes(TW_STOCK_ID) ? [E2E_TW_QUOTE_RESPONSE] : [];
      await fulfillJson(route, items);
      return;
    }

    const stockId = stockIdFromStocksPath(path);
    if (stockId === TW_STOCK_ID) {
      if (path.endsWith("/history") && request.method() === "GET") {
        await fulfillJson(route, { ...E2E_HISTORY_RESPONSE, range: url.searchParams.get("range") ?? "1D" });
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
      if (path.endsWith("/tw-insights") && request.method() === "GET") {
        await fulfillJson(route, E2E_TW_INSIGHTS);
        return;
      }
    }

    await route.continue();
  });
}
