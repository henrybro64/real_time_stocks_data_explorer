export type TimeRange = "1D" | "1W" | "1M";

export type Quote = {
  id: string;
  lastPrice: number;
  change: number;
  changePercent: number;
  previousClose: number;
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
  timestamp: string;
  isMarketOpen: boolean;
};

export type Candle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

