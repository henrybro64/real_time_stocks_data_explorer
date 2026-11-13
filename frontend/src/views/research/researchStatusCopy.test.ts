import { describe, expect, it } from "vitest";

import {
  batchLooksAllFailed,
  drainAllowsAutomaticCollection,
  firstPassNextActionKey,
  firstPassPlainStatusKey,
  normalizeFirstPassCounts,
} from "./researchStatusCopy";

describe("researchStatusCopy", () => {
  it("does not call pending zero-progress jobs running", () => {
    expect(
      firstPassPlainStatusKey({
        status: "running",
        universeSize: 73,
        processedCount: 0,
        cursor: 0,
      }),
    ).toBe("not_started");
    expect(
      firstPassPlainStatusKey({
        status: "pending",
        universeSize: 73,
        processedCount: 0,
      }),
    ).toBe("not_started");
  });

  it("marks snapshot-complete jobs ready for candidates", () => {
    expect(
      firstPassPlainStatusKey({
        status: "completed",
        universeSize: 73,
        processedCount: 73,
        okCount: 60,
        partialCount: 10,
        failedCount: 3,
        activeSnapshotId: "fps_1",
      }),
    ).toBe("ready_for_candidates");
  });

  it("detects all-failed processed batches and overrides status", () => {
    const progress = {
      status: "completed" as const,
      universeSize: 10,
      processedCount: 10,
      okCount: 0,
      partialCount: 0,
      failedCount: 10,
      pendingCount: 0,
    };
    const counts = normalizeFirstPassCounts(progress);
    expect(batchLooksAllFailed(counts)).toBe(true);
    expect(firstPassPlainStatusKey(progress)).toBe("all_failed");
    expect(firstPassNextActionKey("all_failed")).toBe("all_failed");
  });

  it("promises automatic collection only when drain is armed/available", () => {
    expect(drainAllowsAutomaticCollection({ armed: true, available: true })).toBe(
      true,
    );
    expect(drainAllowsAutomaticCollection({ armed: false, available: true })).toBe(
      false,
    );
    expect(drainAllowsAutomaticCollection(null, true)).toBe(true);
    expect(
      firstPassNextActionKey("not_started", {
        drain: { armed: true, available: true },
      }),
    ).toBe("not_started_auto");
    expect(
      firstPassNextActionKey("not_started", {
        drain: { armed: false, available: false },
      }),
    ).toBe("not_started_manual");
  });

  it("uses browsing next-action when viewing a historical job", () => {
    expect(
      firstPassNextActionKey("collecting", { browsingHistorical: true }),
    ).toBe("browsing_historical");
  });

  it("maps budget_exhausted to a dedicated next action", () => {
    expect(firstPassNextActionKey("budget_exhausted")).toBe("budget_exhausted");
  });
});
