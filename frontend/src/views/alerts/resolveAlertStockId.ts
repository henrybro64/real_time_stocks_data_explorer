import type { Stock } from "@shared/api";
import { searchStocks } from "../../services/api/client";

export type AlertStockResolutionCode = "unknown" | "ambiguous";

export class AlertStockResolutionError extends Error {
  readonly code: AlertStockResolutionCode;
  readonly query: string;
  readonly choices: string | null;

  constructor(
    code: AlertStockResolutionCode,
    query: string,
    choices: string | null = null,
  ) {
    const message =
      code === "ambiguous" && choices
        ? `Ambiguous stock symbol "${query}". Choose one of: ${choices}`
        : `Unknown stock id: ${query}`;
    super(message);
    this.name = "AlertStockResolutionError";
    this.code = code;
    this.query = query;
    this.choices = choices;
  }
}

const SUPPORTED_MARKETS = new Set(["US", "TW"]);

function sortedUniqueIds(ids: Iterable<string>, limit?: number): string {
  const unique = [...new Set(ids)].sort((left, right) => left.localeCompare(right));
  return (limit == null ? unique : unique.slice(0, limit)).join(", ");
}

/**
 * Resolve a user-facing ticker or canonical ID to MARKET:SYMBOL before alert submit.
 *
 * Algorithm mirrors backend ``QuoteService.resolve_stock_id``:
 * 1. Canonical ``MARKET:SYMBOL`` (normalize; US symbols uppercased)
 * 2. All-digit bare input → ``TW:{digits}``
 * 3. Prefer ``US:{SYMBOL}`` when search includes that id (backend uses ``get_stock`` first)
 * 4. Exact symbol match / single search hit; refuse silent multi-match picks
 * 5. Bare Latin ticker with no hits → optimistic ``US:{SYMBOL}`` for dynamic US lookup
 */
export async function resolveAlertStockId(
  raw: string,
  search: (query: string) => Promise<Stock[]> = searchStocks,
): Promise<string> {
  const query = raw.trim();
  if (!query) {
    throw new AlertStockResolutionError("unknown", "");
  }

  if (query.includes(":")) {
    const [marketRaw, symbolRaw] = query.split(":", 2);
    const market = marketRaw.trim().toUpperCase();
    const symbol =
      market === "US" ? symbolRaw.trim().toUpperCase() : symbolRaw.trim();
    if (!SUPPORTED_MARKETS.has(market) || !symbol) {
      throw new AlertStockResolutionError("unknown", query);
    }
    return `${market}:${symbol}`;
  }

  if (/^\d+$/.test(query)) {
    return `TW:${query}`;
  }

  const usCandidate = `US:${query.toUpperCase()}`;
  const matches = await search(query);

  const usHit = matches.find((stock) => stock.id.toUpperCase() === usCandidate);
  if (usHit) {
    return usHit.id;
  }

  const exact = matches.filter(
    (stock) => stock.symbol.toUpperCase() === query.toUpperCase(),
  );
  if (exact.length === 1) {
    return exact[0].id;
  }
  if (exact.length > 1) {
    throw new AlertStockResolutionError(
      "ambiguous",
      query,
      sortedUniqueIds(exact.map((stock) => stock.id)),
    );
  }

  if (matches.length === 1) {
    return matches[0].id;
  }
  if (matches.length > 1) {
    throw new AlertStockResolutionError(
      "ambiguous",
      query,
      sortedUniqueIds(
        matches.map((stock) => stock.id),
        5,
      ),
    );
  }

  // No search hits: still normalize bare Latin tickers for backend dynamic US lookup
  // (backend ``get_stock(US:…)`` may succeed when search is empty).
  if (/^[A-Za-z][A-Za-z0-9.-]*$/.test(query)) {
    return usCandidate;
  }

  throw new AlertStockResolutionError("unknown", query);
}
