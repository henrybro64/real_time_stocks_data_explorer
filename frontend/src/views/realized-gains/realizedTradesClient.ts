import type {
  RealizedTrade,
  RealizedTradeCreate,
  RealizedTradeUpdate,
} from "@shared/api";
import { fetchJson, requestJson, requestVoid } from "../../services/api/client";

async function requestJsonWithBody<T>(
  path: string,
  method: "POST" | "PATCH",
  payload: unknown,
): Promise<T> {
  return requestJson<T>(path, {
    method,
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

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
