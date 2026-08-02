export type TimeRange = "1D" | "1W" | "1M" | "3M" | "6M" | "1Y";

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

/** Precomputed 0–100 SVG layout from backend (no client-side price scaling). */
export type ChartPoint = { x: number; y: number };

export type ChartLayout = {
  minY: number;
  maxY: number;
  pointsPolyline: string;
  points: ChartPoint[];
  yAxisTicks: number[];
  xAxisTickIndices: number[];
  firstClose: number;
  lastClose: number;
  trendUp: boolean;
};

export type SmaDirection = "up" | "down" | "flat";

export type SmaPoint = {
  time: string;
  value: number | null;
  direction: SmaDirection | null;
};

export type MovingAverageSeries = {
  sma10: SmaPoint[];
  sma20: SmaPoint[];
  sma60: SmaPoint[];
};

