import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api/client";
import { deriveWatchlistNoticeKind } from "./watchlistState";

describe("deriveWatchlistNoticeKind", () => {
  it("returns an error notice when quote loading fails before any quotes were loaded", () => {
    expect(
      deriveWatchlistNoticeKind({
        hasLoaded: true,
        mutationError: null,
        queryError: null,
        quoteError: new ApiError("provider unavailable", {
          kind: "unavailable",
        }),
        quoteCount: 0,
      }),
    ).toBe("unavailable");
  });

  it("returns stale when a quote refresh fails after prior quotes were loaded", () => {
    expect(
      deriveWatchlistNoticeKind({
        hasLoaded: true,
        mutationError: null,
        queryError: null,
        quoteError: new ApiError("provider unavailable", {
          kind: "unavailable",
        }),
        quoteCount: 1,
      }),
    ).toBe("stale");
  });
});
