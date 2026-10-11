import { describe, expect, it } from "vitest";

import { ERROR_REFETCH_MS, refetchIntervalWhileErroring } from "./pollingInterval";

describe("refetchIntervalWhileErroring", () => {
  it("keeps the normal interval while healthy", () => {
    expect(refetchIntervalWhileErroring("success", 30_000)).toBe(30_000);
  });

  it("keeps polling at a slower rate after an error instead of stopping", () => {
    expect(refetchIntervalWhileErroring("error", 30_000)).toBe(ERROR_REFETCH_MS);
    expect(refetchIntervalWhileErroring("error", 120_000)).toBe(120_000);
  });

  it("leaves disabled polling disabled", () => {
    expect(refetchIntervalWhileErroring("error", false)).toBe(false);
    expect(refetchIntervalWhileErroring("success", undefined)).toBe(false);
  });
});
