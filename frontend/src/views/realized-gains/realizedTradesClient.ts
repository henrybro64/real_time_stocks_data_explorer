import type {
  RealizedTrade,
  RealizedTradeCreate,
  RealizedTradeUpdate,
} from "@shared/api";
import { fetchJson, requestJsonWithBody, requestVoid } from "../../services/api/client";

export function fetchRealizedTrades(currency: "TWD" | "USD") {
  return fetchJson<RealizedTrade[]>(`/realized-trades?currency=${currency}`);
}

export async function createRealizedTrade(payload: RealizedTradeCreate) {
  return requestJsonWithBody<RealizedTrade>("/realized-trades", "POST", payload);
}

export async function updateRealizedTrade(tradeId: number, payload: RealizedTradeUpdate) {
  return requestJsonWithBody<RealizedTrade>(`/realized-trades/${tradeId}`, "PATCH", payload);
}

export async function deleteRealizedTrade(tradeId: number) {
  await requestVoid(`/realized-trades/${tradeId}`, {
    method: "DELETE",
  });
}
