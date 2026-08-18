import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  RANKINGS_REQUEST_TIMEOUT_DETAIL,
  RANKINGS_REQUEST_TIMEOUT_MS,
  fetchRankings,
  refreshRankings,
  fetchTaiwanInstitutionalInvestors,
  fetchTaiwanMarginShort,
  shouldRetryRankingsQuery,
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
    const cancelled = new Error("The user aborted a request.");
    cancelled.name = "AbortError";
    expect(shouldRetryRankingsQuery(0, cancelled)).toBe(false);
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
    expect(JSON.parse(String(init?.body))).toEqual({
      market: "US",
      rankingType: "volume",
      universe: "DJIA",
      limit: 50,
    });
  });
});
