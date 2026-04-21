import { describe, expect, it } from "vitest";

import { getQuoteFreshness } from "./freshness";

describe("getQuoteFreshness", () => {
  const now = new Date("2026-04-21T12:00:00Z");

  it("marks current open-market quotes as live", () => {
    expect(
      getQuoteFreshness("2026-04-21T11:59:00Z", true, { now }),
    ).toBe("live");
  });

  it("marks moderately old open-market quotes as delayed", () => {
    expect(
      getQuoteFreshness("2026-04-21T11:52:00Z", true, { now }),
    ).toBe("delayed");
  });

  it("marks older open-market quotes as stale", () => {
    expect(
      getQuoteFreshness("2026-04-21T11:30:00Z", true, { now }),
    ).toBe("stale");
  });

  it("marks closed-market quotes as closed", () => {
    expect(
      getQuoteFreshness("2026-04-20T20:00:00Z", false, { now }),
    ).toBe("closed");
  });

  it("promotes failed refreshes to stale after a short delay", () => {
    expect(
      getQuoteFreshness("2026-04-21T11:57:00Z", true, { now, refreshFailed: true }),
    ).toBe("stale");
  });
});
