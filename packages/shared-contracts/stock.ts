export type MarketCode = "US" | "TW";

export type CurrencyCode = "USD" | "TWD";

export type Stock = {
  id: string;
  market: MarketCode;
  symbol: string;
  name: string;
  nameEn?: string;
  nameZhTw?: string;
  exchange: string;
  currency: CurrencyCode;
  timezone: string;
};

