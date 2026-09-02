export type RealizedTradeCurrency = "TWD" | "USD";

export type RealizedTrade = {
  id: number;
  symbol: string;
  currency: RealizedTradeCurrency;
  buyPrice: number;
  sellPrice: number;
  shares: number;
  buyDate: string | null;
  sellDate: string;
  realizedGain: number;
  realizedReturnPercent: number;
  createdAt: string;
  updatedAt: string;
};

export type RealizedTradeCreate = {
  symbol: string;
  currency: RealizedTradeCurrency;
  buyPrice: number;
  sellPrice: number;
  shares: number;
  buyDate?: string | null;
  sellDate: string;
};

export type RealizedTradeUpdate = {
  symbol?: string;
  currency?: RealizedTradeCurrency;
  buyPrice?: number;
  sellPrice?: number;
  shares?: number;
  buyDate?: string | null;
  sellDate?: string;
};
