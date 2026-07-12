import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchTaiwanInstitutionalInvestors, fetchTaiwanMarginShort } from "./client";

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
        source: "TWSE official margin and borrowed-short daily data",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchTaiwanMarginShort("TW:1303", 90);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestUrl] = fetchMock.mock.calls[0] ?? [];
    expect(String(requestUrl)).toContain("/api/stocks/TW%3A1303/margin-short?days=90");
  });
});
