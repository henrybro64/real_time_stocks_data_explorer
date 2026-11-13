import { describe, expect, it } from "vitest";

import {
  DRAIN_TICK_FRESH_MS,
  batchLooksAllFailed,
  drainAllowsAutomaticCollection,
  drainTickIsFresh,
  firstPassNextActionKey,
  firstPassPlainStatusKey,
  formatTaipeiDate,
  formatTaipeiDateTime,
  normalizeFailureClass,
  normalizeFirstPassCounts,
} from "./researchStatusCopy";

const NOW = Date.parse("2026-11-13T04:00:00Z");
const freshTick = new Date(NOW - 5 * 60 * 1000).toISOString();
const oldTick = new Date(NOW - 30 * 60 * 1000).toISOString();
const armedFresh = { armed: true, available: true, lastTickAt: freshTick };
const armedStale = { armed: true, available: true, lastTickAt: oldTick };
const armedNoTick = { armed: true, available: true };
const drainOff = { armed: false, available: false };

const unfinished = {
  status: "running",
  universeSize: 73,
  processedCount: 20,
  pendingCount: 53,
  okCount: 20,
  cursor: 20,
};

describe("firstPassPlainStatusKey buckets", () => {
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

  it("collecting_now only when a live lease is held", () => {
    expect(
      firstPassPlainStatusKey(
        { ...unfinished, leaseActive: true },
        { drain: drainOff, now: NOW },
      ),
    ).toBe("collecting_now");
    // First batch in flight (0 processed) is also collecting_now, not not_started.
    expect(
      firstPassPlainStatusKey(
        {
          status: "running",
          universeSize: 73,
          processedCount: 0,
          cursor: 0,
          leaseActive: true,
        },
        { drain: drainOff, now: NOW },
      ),
    ).toBe("collecting_now");
  });

  it("never reports collecting_now when the data is stale", () => {
    expect(
      firstPassPlainStatusKey(
        { ...unfinished, leaseActive: true },
        { drain: armedFresh, now: NOW, stale: true },
      ),
    ).toBe("paused_auto");
  });

  it("running + cursor>0 + no lease + drain off → paused_manual (never collecting)", () => {
    const bucket = firstPassPlainStatusKey(
      { ...unfinished, leaseActive: false },
      { drain: drainOff, now: NOW },
    );
    expect(bucket).toBe("paused_manual");
    expect(bucket).not.toMatch(/collecting/);
    // Missing leaseActive (older API) is treated as no lease.
    expect(
      firstPassPlainStatusKey(unfinished, { drain: null, now: NOW }),
    ).toBe("paused_manual");
  });

  it("paused_auto when armed+available and the last tick is within 15 minutes", () => {
    expect(
      firstPassPlainStatusKey(unfinished, { drain: armedFresh, now: NOW }),
    ).toBe("paused_auto");
    expect(DRAIN_TICK_FRESH_MS).toBe(15 * 60 * 1000);
    const edge = { armed: true, available: true, lastTickAt: new Date(NOW - DRAIN_TICK_FRESH_MS).toISOString() };
    expect(firstPassPlainStatusKey(unfinished, { drain: edge, now: NOW })).toBe(
      "paused_auto",
    );
  });

  it("paused_auto_stale when armed but tick is old or missing", () => {
    expect(
      firstPassPlainStatusKey(unfinished, { drain: armedStale, now: NOW }),
    ).toBe("paused_auto_stale");
    expect(
      firstPassPlainStatusKey(unfinished, { drain: armedNoTick, now: NOW }),
    ).toBe("paused_auto_stale");
    expect(
      firstPassPlainStatusKey(unfinished, {
        drain: { armed: true, available: true, lastTickAt: "not-a-date" },
        now: NOW,
      }),
    ).toBe("paused_auto_stale");
  });

  it("paused_manual when drain is armed but unavailable or disarmed", () => {
    expect(
      firstPassPlainStatusKey(unfinished, {
        drain: { armed: true, available: false, lastTickAt: freshTick },
        now: NOW,
      }),
    ).toBe("paused_manual");
    expect(
      firstPassPlainStatusKey(unfinished, {
        drain: { armed: false, available: true, lastTickAt: freshTick },
        now: NOW,
      }),
    ).toBe("paused_manual");
  });

  it("uses scheduleEnabled only when firstPassDrain is absent", () => {
    expect(
      firstPassPlainStatusKey(unfinished, {
        drain: null,
        scheduleEnabled: true,
        now: NOW,
      }),
    ).toBe("paused_auto_stale");
    // Drain present and off wins over a legacy schedule flag.
    expect(
      firstPassPlainStatusKey(unfinished, {
        drain: drainOff,
        scheduleEnabled: true,
        now: NOW,
      }),
    ).toBe("paused_manual");
    expect(drainAllowsAutomaticCollection(drainOff, true)).toBe(false);
    expect(drainAllowsAutomaticCollection(null, true)).toBe(true);
    expect(drainAllowsAutomaticCollection(undefined, false)).toBe(false);
  });

  it("accepts a function clock", () => {
    expect(drainTickIsFresh(armedFresh, () => NOW)).toBe(true);
    expect(drainTickIsFresh(armedFresh, () => new Date(NOW + 60 * 60 * 1000))).toBe(
      false,
    );
  });

  it("keeps waiting_retry / failed / budget_exhausted", () => {
    expect(
      firstPassPlainStatusKey({ ...unfinished, status: "deferred", failedCount: 3 }),
    ).toBe("waiting_retry");
    expect(
      firstPassPlainStatusKey({
        status: "failed",
        universeSize: 73,
        processedCount: 20,
        okCount: 15,
        failedCount: 5,
      }),
    ).toBe("failed");
    expect(
      firstPassPlainStatusKey({ status: "budget_exhausted", universeSize: 5 }),
    ).toBe("budget_exhausted");
    expect(firstPassPlainStatusKey(null)).toBe("idle");
    expect(firstPassPlainStatusKey({ status: "" })).toBe("idle");
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

  it("completed 73/73 with only failures is all_failed (terminal)", () => {
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
    expect(
      firstPassPlainStatusKey({ ...progress, status: "partial" }),
    ).toBe("all_failed");
    expect(firstPassPlainStatusKey({ ...progress, status: "failed" })).toBe(
      "all_failed",
    );
    expect(firstPassNextActionKey("all_failed")).toBe("all_failed");
  });

  it("unfinished job with only failures so far is NOT all_failed", () => {
    const onlyFailures = {
      status: "running" as const,
      universeSize: 73,
      processedCount: 10,
      okCount: 0,
      partialCount: 0,
      failedCount: 10,
      pendingCount: 63,
      cursor: 10,
    };
    expect(firstPassPlainStatusKey(onlyFailures, { drain: drainOff, now: NOW })).toBe(
      "paused_manual",
    );
    expect(
      firstPassPlainStatusKey(onlyFailures, { drain: armedFresh, now: NOW }),
    ).toBe("paused_auto");
    expect(
      firstPassPlainStatusKey(
        { ...onlyFailures, leaseActive: true },
        { drain: drainOff, now: NOW },
      ),
    ).toBe("collecting_now");
    expect(
      firstPassPlainStatusKey({ ...onlyFailures, status: "deferred" }),
    ).toBe("waiting_retry");
    // `partial` with symbols still pending is unfinished, not terminal.
    expect(
      firstPassPlainStatusKey(
        { ...onlyFailures, status: "partial" },
        { drain: drainOff, now: NOW },
      ),
    ).toBe("paused_manual");
  });

  it("73/73 with gaps and no snapshot is not shown as ready or complete", () => {
    const bucket = firstPassPlainStatusKey(
      {
        status: "completed",
        universeSize: 73,
        processedCount: 73,
        okCount: 60,
        partialCount: 5,
        failedCount: 8,
        pendingCount: 0,
      },
      { drain: drainOff, now: NOW },
    );
    expect(bucket).not.toBe("ready_for_candidates");
    expect(bucket).not.toBe("collecting_now");
  });
});

describe("firstPassNextActionKey", () => {
  it("promises automatic collection only when drain is armed/available", () => {
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
    expect(
      firstPassNextActionKey("not_started", {
        drain: drainOff,
        scheduleEnabled: true,
      }),
    ).toBe("not_started_manual");
  });

  it("passes new buckets through and never maps pauses to collecting", () => {
    for (const bucket of [
      "collecting_now",
      "paused_auto",
      "paused_auto_stale",
      "paused_manual",
    ]) {
      expect(firstPassNextActionKey(bucket)).toBe(bucket);
    }
    expect(firstPassNextActionKey("collecting")).toBe("default");
  });

  it("uses browsing next-action when viewing a historical job", () => {
    expect(
      firstPassNextActionKey("collecting_now", { browsingHistorical: true }),
    ).toBe("browsing_historical");
  });

  it("maps budget_exhausted to a dedicated next action", () => {
    expect(firstPassNextActionKey("budget_exhausted")).toBe("budget_exhausted");
  });
});

describe("Taipei time formatting", () => {
  it("formats in Asia/Taipei with the active locale and never returns raw ISO", () => {
    const iso = "2026-11-13T04:05:00Z"; // 12:05 in Taipei (UTC+8)
    const zh = formatTaipeiDateTime(iso, "zh-TW");
    const en = formatTaipeiDateTime(iso, "en-US");
    expect(zh).toMatch(/2026/);
    expect(zh).toMatch(/12:05/);
    expect(en).toMatch(/12:05/);
    expect(zh).not.toMatch(/T04:05/);
    expect(en).not.toMatch(/Z$/);
    expect(zh).not.toBe(en);
  });

  it("rolls the Taipei calendar date over midnight UTC+8", () => {
    expect(formatTaipeiDate("2026-11-12T17:00:00Z", "en-US")).toBe("11/13/2026");
    expect(formatTaipeiDate("2026-11-12T17:00:00Z", "zh-TW")).toBe("2026/11/13");
  });

  it("returns empty string for missing or invalid input", () => {
    expect(formatTaipeiDateTime(null, "zh-TW")).toBe("");
    expect(formatTaipeiDateTime("", "zh-TW")).toBe("");
    expect(formatTaipeiDateTime("garbage", "en-US")).toBe("");
    expect(formatTaipeiDate(undefined, "en-US")).toBe("");
  });
});

describe("normalizeFailureClass", () => {
  it("maps backend failure classes to four investor-facing groups", () => {
    expect(normalizeFailureClass("timeout")).toBe("transient");
    expect(normalizeFailureClass("transient")).toBe("transient");
    expect(normalizeFailureClass(undefined)).toBe("transient");
    expect(normalizeFailureClass("rate_limited")).toBe("rate_limited");
    expect(normalizeFailureClass("programming_error")).toBe("programming_error");
    expect(normalizeFailureClass("permanent")).toBe("permanent");
  });
});
