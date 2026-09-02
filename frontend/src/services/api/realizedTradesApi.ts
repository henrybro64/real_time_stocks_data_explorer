import type { RealizedTrade, RealizedTradeCreate, RealizedTradeUpdate } from "@shared/api";

import { requestJson, requestVoid } from "./client";

export function fetchRealizedTrades(currency: "TWD" | "USD") {
  return requestJson<RealizedTrade[]>(`/realized-trades?currency=${currency}`);
}

export async function createRealizedTrade(payload: RealizedTradeCreate) {
  return requestJson<RealizedTrade>("/realized-trades", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function updateRealizedTrade(tradeId: number, payload: RealizedTradeUpdate) {
  return requestJson<RealizedTrade>(`/realized-trades/${tradeId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function deleteRealizedTrade(tradeId: number) {
  await requestVoid(`/realized-trades/${tradeId}`, {
    method: "DELETE",
  });
}
