import type { Route } from "@playwright/test";

import { previewRealizedGain } from "../../src/views/realized-gains/realizedGainCalculations";

export type RealizedTradeRow = {
  id: number;
  symbol: string;
  currency: "TWD" | "USD";
  buyPrice: number;
  sellPrice: number;
  shares: number;
  buyDate: string | null;
  sellDate: string;
  cumulativeDividends: number;
  priceGain: number;
  realizedGain: number;
  realizedReturnPercent: number;
  createdAt: string;
  updatedAt: string;
};

type RealizedTradePayload = {
  symbol?: string;
  currency?: "TWD" | "USD";
  buyPrice?: number;
  sellPrice?: number;
  shares?: number;
  buyDate?: string | null;
  sellDate?: string;
  cumulativeDividends?: number;
};

export type RealizedTradeMockSlice = {
  realizedTrades: RealizedTradeRow[];
  nextRealizedTradeId: number;
};

function buildRealizedTradeRow(
  state: RealizedTradeMockSlice,
  payload: RealizedTradePayload,
  current?: RealizedTradeRow,
): RealizedTradeRow {
  const timestamp = new Date().toISOString();
  const buyPrice = payload.buyPrice ?? current?.buyPrice ?? 0;
  const sellPrice = payload.sellPrice ?? current?.sellPrice ?? 0;
  const shares = payload.shares ?? current?.shares ?? 0;
  const cumulativeDividends =
    payload.cumulativeDividends ?? current?.cumulativeDividends ?? 0;
  const metrics = previewRealizedGain(buyPrice, sellPrice, shares, cumulativeDividends) ?? {
    priceGain: 0,
    cumulativeDividends: 0,
    realizedGain: 0,
    realizedReturnPercent: 0,
  };
  return {
    id: current?.id ?? state.nextRealizedTradeId++,
    symbol: (payload.symbol ?? current?.symbol ?? "").trim().toUpperCase(),
    currency: payload.currency ?? current?.currency ?? "TWD",
    buyPrice,
    sellPrice,
    shares,
    buyDate: payload.buyDate === undefined ? current?.buyDate ?? null : payload.buyDate,
    sellDate: payload.sellDate ?? current?.sellDate ?? timestamp.slice(0, 10),
    cumulativeDividends: metrics.cumulativeDividends,
    priceGain: metrics.priceGain,
    realizedGain: metrics.realizedGain,
    realizedReturnPercent: metrics.realizedReturnPercent,
    createdAt: current?.createdAt ?? timestamp,
    updatedAt: timestamp,
  };
}

const REALIZED_TRADE_PATH = /^\/api\/realized-trades\/(\d+)$/;

export async function handleRealizedTradeRoute(
  route: Route,
  path: string,
  url: URL,
  state: RealizedTradeMockSlice,
  fulfillJson: (route: Route, body: unknown, status?: number) => Promise<void>,
): Promise<boolean> {
  const request = route.request();

  if (path === "/api/realized-trades" && request.method() === "GET") {
    const currency = url.searchParams.get("currency");
    const rows = state.realizedTrades
      .filter((row) => row.currency === currency)
      .sort((left, right) => right.sellDate.localeCompare(left.sellDate) || right.id - left.id);
    await fulfillJson(route, rows);
    return true;
  }

  if (path === "/api/realized-trades" && request.method() === "POST") {
    const payload = request.postDataJSON() as RealizedTradePayload;
    const created = buildRealizedTradeRow(state, payload);
    state.realizedTrades.push(created);
    await fulfillJson(route, created);
    return true;
  }

  const realizedTradeMatch = REALIZED_TRADE_PATH.exec(path);
  if (realizedTradeMatch) {
    const tradeId = Number(realizedTradeMatch[1]);
    const index = state.realizedTrades.findIndex((row) => row.id === tradeId);
    if (index < 0) {
      await fulfillJson(route, { detail: `Realized trade not found: ${tradeId}` }, 404);
      return true;
    }

    if (request.method() === "PATCH") {
      const payload = request.postDataJSON() as RealizedTradePayload;
      state.realizedTrades[index] = buildRealizedTradeRow(
        state,
        payload,
        state.realizedTrades[index],
      );
      await fulfillJson(route, state.realizedTrades[index]);
      return true;
    }

    if (request.method() === "DELETE") {
      state.realizedTrades.splice(index, 1);
      await route.fulfill({ status: 204, body: "" });
      return true;
    }
  }

  return false;
}
