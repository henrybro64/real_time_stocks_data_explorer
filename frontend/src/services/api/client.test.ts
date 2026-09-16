import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  RANKINGS_REQUEST_TIMEOUT_DETAIL,
  RANKINGS_REQUEST_TIMEOUT_MS,
  fetchRankings,
  fetchRankingsHistory,
  fetchRankingsHistoryDates,
  historyDatesRetryDelayMs,
  parseRetryAfterSeconds,
  refreshRankings,
  fetchTaiwanInstitutionalInvestors,
  fetchTaiwanMarginShort,
  fetchTaiwanOwnershipConcentration,
  fetchTaiwanProfitability,
  shouldRetryHistoryDatesQuery,
  shouldRetryRankingsQuery,
  shouldRetryStockDetailQuery,
  stockDetailRetryDelayMs,
  toApiError,
} from "./client";

function emptyUsRankingJson(overrides: Record<string, unknown> = {}) {
  return {
    market: "US",
    rankingType: "volume",
    items: [],
    timestamp: "2026-08-16T00:00:00Z",
    source: "test",
    sourceType: "free-unofficial",
    isMarketOpen: false,
    dataFreshnessLabel: "ok",
    warningMessage: null,
    unsupportedReason: null,
    ...overrides,
  };
}

describe("fetchTaiwanInstitutionalInvestors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes the stock id and passes the days query param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        stockId: "TW:2330",
        symbol: "2330",
        market: "TWSE",
        latestDate: null,
        summary: null,
        history: [],
        sharesPerLot: 1000,
        source: "TWSE T86 daily institutional data",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchTaiwanInstitutionalInvestors("TW:2330", 90);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestUrl] = fetchMock.mock.calls[0] ?? [];
    expect(String(requestUrl)).toContain("/api/stocks/TW%3A2330/institutional-investors?days=90");
  });
});

describe("fetchTaiwanMarginShort", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes the stock id and passes the days query param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        stockId: "TW:1303",
        symbol: "1303",
        market: "TWSE",
        latestDate: null,
        summary: null,
        history: [],
        sharesPerLot: 1000,
        source: "TWSE official margin and borrowed-short daily data",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchTaiwanMarginShort("TW:1303", 90);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestUrl] = fetchMock.mock.calls[0] ?? [];
    expect(String(requestUrl)).toContain("/api/stocks/TW%3A1303/margin-short?days=90");
  });

  it.each([
    {
      status: 502,
      detail: "Taiwan margin-short official source denied access.",
      kind: "permission",
    },
    {
      status: 503,
      detail: "Taiwan margin-short official source is rate-limited.",
      kind: "unavailable",
    },
    {
      status: 502,
      detail: "Taiwan margin-short official response could not be interpreted.",
      kind: "unavailable",
    },
  ] as const)("classifies margin-short $status $kind failures from detail text", async ({ status, detail, kind }) => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status,
      json: async () => ({ detail }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchTaiwanMarginShort("TW:1303", 90)).rejects.toMatchObject({
      status,
      detail,
      kind,
    });
  });
});

describe("fetchTaiwanOwnershipConcentration", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes the stock id and passes the range query param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        stockId: "TW:2330",
        range: "1Y",
        asOf: null,
        source: "tdcc+twse_mi_qfiis",
        stale: false,
        points: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchTaiwanOwnershipConcentration("TW:2330", "6M");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestUrl] = fetchMock.mock.calls[0] ?? [];
    expect(String(requestUrl)).toContain(
      "/api/stocks/TW%3A2330/tw-ownership-concentration?range=6M",
    );
  });
});

describe("fetchTaiwanProfitability", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes the stock id and passes the quarters query param", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        stockId: "TW:2330",
        quarters: 12,
        asOf: null,
        source: "yahoo_finance_quarterly_income",
        stale: false,
        points: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchTaiwanProfitability("TW:2330", 8);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestUrl] = fetchMock.mock.calls[0] ?? [];
    expect(String(requestUrl)).toContain("/api/stocks/TW%3A2330/tw-profitability?quarters=8");
  });

  it("classifies HTTP 422 as validation", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      headers: { get: () => null },
      json: async () => ({
        detail: [
          {
            type: "literal_error",
            loc: ["query", "quarters"],
            msg: "Input should be 8, 12 or 20",
            input: "12",
          },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchTaiwanProfitability("TW:2330", 12)).rejects.toMatchObject({
      status: 422,
      kind: "validation",
    });
  });

  it("classifies HTTP 400 as validation", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      headers: { get: () => null },
      json: async () => ({ detail: "quarters must be 8, 12, or 20" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchTaiwanProfitability("TW:2330", 12)).rejects.toMatchObject({
      status: 400,
      kind: "validation",
      detail: "quarters must be 8, 12, or 20",
    });
  });
});

describe("shouldRetryStockDetailQuery", () => {
  it("does not retry validation 400/422 or not-found/auth outcomes", () => {
    expect(
      shouldRetryStockDetailQuery(
        0,
        new ApiError("bad quarters", { status: 422, kind: "validation" }),
      ),
    ).toBe(false);
    expect(
      shouldRetryStockDetailQuery(
        0,
        new ApiError("bad quarters", { status: 400, kind: "validation" }),
      ),
    ).toBe(false);
    expect(
      shouldRetryStockDetailQuery(
        0,
        new ApiError("missing", { status: 404, kind: "not_found" }),
      ),
    ).toBe(false);
    expect(
      shouldRetryStockDetailQuery(
        0,
        new ApiError("login", { status: 401, kind: "auth" }),
      ),
    ).toBe(false);
  });

  it("retries transient 502/503 within a bounded budget", () => {
    const upstream = new ApiError("upstream", { status: 502, kind: "unavailable" });
    expect(shouldRetryStockDetailQuery(0, upstream)).toBe(true);
    expect(shouldRetryStockDetailQuery(1, upstream)).toBe(true);
    expect(shouldRetryStockDetailQuery(2, upstream)).toBe(false);
  });

  it("retries status-less network failures including Safari Load failed", () => {
    expect(shouldRetryStockDetailQuery(0, new TypeError("Load failed"))).toBe(true);
    expect(shouldRetryStockDetailQuery(0, new TypeError("Failed to fetch"))).toBe(true);
    expect(
      shouldRetryStockDetailQuery(
        0,
        new ApiError("Load failed", { status: null, kind: "unavailable" }),
      ),
    ).toBe(true);
  });
});

describe("stockDetailRetryDelayMs", () => {
  it("honors Retry-After seconds on 429", () => {
    const rateLimited = new ApiError("slow down", {
      status: 429,
      kind: "unavailable",
      retryAfterSeconds: 5,
    });
    expect(stockDetailRetryDelayMs(0, rateLimited)).toBe(5_000);
  });

  it("falls back to bounded backoff without Retry-After", () => {
    expect(stockDetailRetryDelayMs(0)).toBe(1_000);
    expect(stockDetailRetryDelayMs(1)).toBe(2_000);
  });
});

describe("toApiError", () => {
  it("maps TypeError network failures to unavailable", () => {
    expect(toApiError(new TypeError("Load failed"))).toMatchObject({
      kind: "unavailable",
      status: null,
      message: "Load failed",
    });
  });
});

describe("fetchRankings", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("aborts a stalled request and maps the timeout to unavailable", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          const error = new Error("The user aborted a request.");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const pending = expect(fetchRankings("TW", "volume", 50, "TWSE")).rejects.toMatchObject({
      name: "ApiError",
      kind: "unavailable",
      detail: RANKINGS_REQUEST_TIMEOUT_DETAIL,
      status: null,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    expect(init?.signal?.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(RANKINGS_REQUEST_TIMEOUT_MS);
    await pending;
    expect(init?.signal?.aborted).toBe(true);
  });

  it("does not treat a timely rankings response as a timeout", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        market: "TW",
        rankingType: "volume",
        items: [],
        timestamp: "2026-08-16T00:00:00Z",
        source: "test",
        sourceType: "free-unofficial",
        isMarketOpen: true,
        dataFreshnessLabel: "ok",
        warningMessage: null,
        unsupportedReason: null,
        usRankingsScope: null,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchRankings("TW", "volume", 50, "TWSE")).resolves.toMatchObject({
      market: "TW",
      rankingType: "volume",
    });
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.signal).toBeInstanceOf(
      AbortSignal,
    );
  });

  it("keeps HTTP unavailable errors distinct from a client timeout", async () => {
    const detail = "TW institutional daily data is temporarily unavailable from the free public source.";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ detail }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchRankings("TW", "net_buy", 50, "TWSE")).rejects.toMatchObject({
      kind: "unavailable",
      status: 502,
      detail,
    });
  });

  it("does not retry a client timeout, but still retries other unavailable errors", () => {
    const timeout = new ApiError(RANKINGS_REQUEST_TIMEOUT_DETAIL, {
      kind: "unavailable",
      status: null,
      detail: RANKINGS_REQUEST_TIMEOUT_DETAIL,
    });
    const upstream = new ApiError(
      "TW institutional daily data is temporarily unavailable from the free public source.",
      {
        kind: "unavailable",
        status: 502,
        detail: "TW institutional daily data is temporarily unavailable from the free public source.",
      },
    );

    expect(shouldRetryRankingsQuery(0, timeout)).toBe(false);
    expect(shouldRetryRankingsQuery(0, upstream)).toBe(true);
    expect(shouldRetryRankingsQuery(2, upstream)).toBe(true);
    expect(shouldRetryRankingsQuery(3, upstream)).toBe(false);
    expect(
      shouldRetryRankingsQuery(
        0,
        new ApiError("No archived rankings", {
          status: 404,
          detail: "No archived rankings",
          kind: "not_found",
        }),
      ),
    ).toBe(false);
    expect(
      shouldRetryRankingsQuery(
        0,
        new ApiError("Invalid universe", {
          status: 422,
          detail: "Invalid universe",
          kind: "validation",
        }),
      ),
    ).toBe(false);
    const cancelled = new Error("The user aborted a request.");
    cancelled.name = "AbortError";
    expect(shouldRetryRankingsQuery(0, cancelled)).toBe(false);
  });

  it("retries history-date 503s in a bounded way and skips client errors", () => {
    const unavailable = new ApiError("archive waking", {
      kind: "unavailable",
      status: 503,
      detail: "Rankings archive is unavailable",
    });
    expect(shouldRetryHistoryDatesQuery(0, unavailable)).toBe(true);
    expect(shouldRetryHistoryDatesQuery(3, unavailable)).toBe(true);
    expect(shouldRetryHistoryDatesQuery(4, unavailable)).toBe(false);
    expect(
      shouldRetryHistoryDatesQuery(
        0,
        new ApiError("missing", { status: 404, kind: "not_found" }),
      ),
    ).toBe(false);
    expect(
      shouldRetryHistoryDatesQuery(
        0,
        new ApiError("bad", { status: 422, kind: "validation" }),
      ),
    ).toBe(false);
    expect(
      shouldRetryHistoryDatesQuery(
        0,
        new ApiError("sql bug", {
          status: 500,
          kind: "unknown",
          detail: "Rankings archive query failed",
        }),
      ),
    ).toBe(false);
    expect(historyDatesRetryDelayMs(1)).toBe(8_000);
    expect(historyDatesRetryDelayMs(2)).toBe(16_000);
    expect(
      historyDatesRetryDelayMs(
        1,
        new ApiError("archive waking", {
          kind: "unavailable",
          status: 503,
          retryAfterSeconds: 10,
        }),
      ),
    ).toBe(10_000);
    expect(parseRetryAfterSeconds("10")).toBe(10);
    expect(parseRetryAfterSeconds("")).toBeNull();
  });

  it("preserves Retry-After from archive 503 responses", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      headers: new Headers({ "Retry-After": "10" }),
      json: async () => ({ detail: "Rankings archive is unavailable" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchRankingsHistoryDates("US", "DJIA", "volume")).rejects.toMatchObject({
      status: 503,
      retryAfterSeconds: 10,
      kind: "unavailable",
    });
  });

  it("does not map a caller abort to a rankings timeout", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          const error = new Error("The user aborted a request.");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    const pending = fetchRankings("TW", "volume", 50, "TWSE", controller.signal);
    const assertion = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await vi.advanceTimersByTimeAsync(RANKINGS_REQUEST_TIMEOUT_MS);
    await assertion;
    await expect(pending).rejects.not.toMatchObject({
      detail: RANKINGS_REQUEST_TIMEOUT_DETAIL,
    });
  });

  it("sends the selected U.S. universe on GET rankings", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => emptyUsRankingJson({
        usRankingsScope: "nasdaq100",
        universe: "NASDAQ100",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchRankings("US", "volume", 50, "NASDAQ100");
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("universe=NASDAQ100");
    expect(url).not.toContain("twBoard=");
  });
});

describe("fetchRankingsHistoryDates", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests newest archived trading dates for a universe", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        market: "US",
        universe: "DJIA",
        dates: ["2026-08-20", "2026-08-19"],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchRankingsHistoryDates("US", "DJIA", "volume", 5)).resolves.toMatchObject({
      dates: ["2026-08-20", "2026-08-19"],
    });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/rankings/history/dates?");
    expect(url).toContain("market=US");
    expect(url).toContain("universe=DJIA");
    expect(url).toContain("type=volume");
    expect(url).toContain("limit=5");
  });
});

describe("fetchRankingsHistory", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests one archived EOD ranking list by trading date", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () =>
        emptyUsRankingJson({
          tradingDate: "2026-08-20",
          captureScope: "end_of_day",
          universe: "DJIA",
          usRankingsScope: "djia",
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchRankingsHistory("US", "volume", "2026-08-20", 50, "DJIA"),
    ).resolves.toMatchObject({
      tradingDate: "2026-08-20",
      captureScope: "end_of_day",
    });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/rankings/history?");
    expect(url).toContain("date=2026-08-20");
    expect(url).toContain("universe=DJIA");
    expect(url).not.toContain("twBoard=");
  });
});

describe("refreshRankings", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("posts the selected universe without overloading GET", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () =>
        emptyUsRankingJson({
          timestamp: "2026-07-03T16:00:00-04:00",
          source: "DJIA constituent quote rankings from the app's free best-effort quote providers.",
          dataFreshnessLabel: "Market closed; rankings use the latest available quote snapshot.",
          usRankingsScope: "djia",
          universe: "DJIA",
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(refreshRankings("US", "volume", "DJIA")).resolves.toMatchObject({
      universe: "DJIA",
      usRankingsScope: "djia",
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/rankings/refresh");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body ?? ""))).toEqual({
      market: "US",
      rankingType: "volume",
      universe: "DJIA",
      limit: 50,
    });
  });
});
