import catalog from "./marketIndexCatalog.json";

export type MarketIndexCatalogEntry = {
  id: string;
  market: "TW" | "US" | "CRYPTO";
  label: string;
  labelZhTw: string;
};

export const MARKET_INDEX_CATALOG: ReadonlyArray<MarketIndexCatalogEntry> =
  catalog.indices as ReadonlyArray<MarketIndexCatalogEntry>;
