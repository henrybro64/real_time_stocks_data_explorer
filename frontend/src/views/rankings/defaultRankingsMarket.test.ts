import type { MarketStatus } from "@shared/api";
import { describe, expect, it } from "vitest";

import { defaultRankingsMarketFromStatuses } from "./defaultRankingsMarket";

function status(partial: Partial<MarketStatus> & Pick<MarketStatus, "market">): MarketStatus {
  return {
    label: "",
    timezone: "",
    isOpen: false,
    localTime: "",
    withinHourBeforeOpen: false,
    ...partial,
  };
}

describe("defaultRankingsMarketFromStatuses", () => {
  it("prefers TW when both markets are within the hour before open", () => {
    expect(
      defaultRankingsMarketFromStatuses([
        status({ market: "TW", withinHourBeforeOpen: true }),
        status({ market: "US", withinHourBeforeOpen: true }),
      ]),
    ).toBe("TW");
  });

  it("chooses US when only US is within the hour before open", () => {
    expect(
      defaultRankingsMarketFromStatuses([
        status({ market: "TW", withinHourBeforeOpen: false }),
        status({ market: "US", withinHourBeforeOpen: true }),
      ]),
    ).toBe("US");
  });

  it("chooses TW when only TW is within the hour before open", () => {
    expect(
      defaultRankingsMarketFromStatuses([
        status({ market: "TW", withinHourBeforeOpen: true }),
        status({ market: "US", withinHourBeforeOpen: false }),
      ]),
    ).toBe("TW");
  });

  it("defaults to TW when neither pre-open flag is set", () => {
    expect(
      defaultRankingsMarketFromStatuses([
        status({ market: "TW", withinHourBeforeOpen: false }),
        status({ market: "US", withinHourBeforeOpen: false }),
      ]),
    ).toBe("TW");
  });
});
