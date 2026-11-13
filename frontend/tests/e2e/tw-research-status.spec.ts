import {
  setE2eAuthenticatedUser,
  setE2eTwResearchFirstPass,
  type E2eTwResearchFirstPassFixture,
} from "./e2eApiMocks";
import { expect, test } from "./setup";

/**
 * #350/#351 — investor-facing first-pass status scenarios (mocked).
 */

const JOB_ID = "fpj_e2e_status";
const RUN_ID = "rr_e2e_status";

function baseJob(overrides: Record<string, unknown> = {}) {
  return {
    jobId: JOB_ID,
    status: "running",
    qualityStatus: "incomplete",
    createdAt: "2026-11-13T02:00:00Z",
    updatedAt: "2026-11-13T03:50:00Z",
    activeSnapshotId: null,
    universeSize: 73,
    cursor: 20,
    ...overrides,
  };
}

function baseProgress(overrides: Record<string, unknown> = {}) {
  return {
    jobId: JOB_ID,
    status: "running",
    qualityStatus: "incomplete",
    universeSize: 73,
    cursor: 20,
    processedCount: 20,
    pendingCount: 53,
    okCount: 20,
    partialCount: 0,
    failedCount: 0,
    deferredCount: 0,
    activeSnapshotId: null,
    leaseActive: false,
    leaseExpiresAt: null,
    updatedAt: "2026-11-13T03:50:00Z",
    nextRetryAt: null,
    ...overrides,
  };
}

function baseRun(overrides: Record<string, unknown> = {}) {
  return {
    researchRunId: RUN_ID,
    userId: "status-user",
    phase: "phase1_first_pass",
    status: "running",
    firstPassJobId: JOB_ID,
    createdAt: "2026-11-13T02:00:00Z",
    scheduleEnabled: false,
    firstPassDrain: { armed: false, available: false },
    ...overrides,
  };
}

function fixture(partial: Partial<E2eTwResearchFirstPassFixture>): E2eTwResearchFirstPassFixture {
  return {
    researchRun: baseRun(),
    jobs: [baseJob()],
    progress: baseProgress(),
    batchResult: null,
    symbolsResult: null,
    ...partial,
  };
}

async function openResearch(
  page: import("@playwright/test").Page,
  options?: { language?: "zh-TW" | "en"; viewport?: { width: number; height: number } },
) {
  setE2eAuthenticatedUser(page, { id: "status-user", username: "status-user" });
  if (options?.viewport) {
    await page.setViewportSize(options.viewport);
  }
  await page.addInitScript((lang) => {
    window.localStorage.setItem("stock-ticker-language", lang);
  }, options?.language ?? "zh-TW");
  await page.goto("/research/tw");
  await expect(page.locator("#main-content")).toBeVisible();
  await expect(page.getByTestId("tw-research-investor-summary")).toBeVisible();
}

test("empty / first-time: idle status and start action", async ({ page }) => {
  setE2eTwResearchFirstPass(page, {
    researchRun: null,
    jobs: [],
    progress: null,
  });
  await openResearch(page);
  await expect(page.getByTestId("tw-research-plain-status")).toHaveAttribute(
    "data-bucket",
    "idle",
  );
  await expect(page.getByTestId("tw-research-next-action")).toContainText("開始研究");
  await expect(page.getByRole("button", { name: "開始研究" })).toBeEnabled();
});

test("0/73 not started", async ({ page }) => {
  setE2eTwResearchFirstPass(
    page,
    fixture({
      jobs: [baseJob({ status: "pending", cursor: 0 })],
      progress: baseProgress({
        status: "pending",
        cursor: 0,
        processedCount: 0,
        pendingCount: 73,
        okCount: 0,
      }),
    }),
  );
  await openResearch(page);
  await expect(page.getByTestId("tw-research-plain-status")).toHaveAttribute(
    "data-bucket",
    "not_started",
  );
  await expect(page.getByTestId("tw-research-investor-counts")).toContainText("研究股票 73");
  await expect(page.getByTestId("tw-research-investor-counts")).toContainText("已處理 0");
});

test("paused_manual when running without a live lease", async ({ page }) => {
  setE2eTwResearchFirstPass(
    page,
    fixture({
      progress: baseProgress({ leaseActive: false, cursor: 20 }),
    }),
  );
  await openResearch(page);
  await expect(page.getByTestId("tw-research-plain-status")).toHaveAttribute(
    "data-bucket",
    "paused_manual",
  );
  await expect(page.getByTestId("tw-research-plain-status")).toContainText(
    "目前暫停：沒有正在執行的收集",
  );
  await expect(page.getByTestId("tw-research-plain-status")).not.toContainText("正在收集");
});

test("10/10 batch failed shows names and plain reasons", async ({ page }) => {
  const batchIds = Array.from({ length: 10 }, (_, i) => `TW:${2330 + i}`);
  setE2eTwResearchFirstPass(
    page,
    fixture({
      progress: baseProgress({ okCount: 20, failedCount: 0, processedCount: 20 }),
      batchResult: {
        jobId: JOB_ID,
        status: "running",
        batchIds,
        batchCounts: { ok: 0, partial: 0, failed: 10 },
        processed: 10,
        leaseHeld: false,
        leaseLost: false,
        budgetStopped: false,
      },
      symbolsResult: {
        jobId: JOB_ID,
        stockIdsFilter: batchIds,
        records: [
          {
            stockId: "TW:2330",
            stockName: "台積電",
            nameStatus: "ok",
            evidence: { stockName: "台積電", nameStatus: "ok", failureClass: "timeout" },
          },
          {
            stockId: "TW:2331",
            stockName: null,
            nameStatus: "unavailable",
            evidence: { nameStatus: "unavailable", failureClass: "rate_limited" },
          },
        ],
      },
    }),
  );
  await openResearch(page);
  await expect(page.getByTestId("tw-research-plain-status")).toHaveAttribute(
    "data-bucket",
    "paused_manual",
  );
  // Batch button lives under Advanced in the evidence tab.
  const batchBtn = page.getByTestId("tw-research-run-first-pass-batch");
  await batchBtn.locator("xpath=ancestor::details[@data-testid='tw-research-advanced-details']").locator("summary").click();
  await batchBtn.click();
  await expect(page.getByTestId("tw-research-batch-all-failed")).toContainText(
    "本批 10 檔資料收集失敗",
  );
  await expect(page.getByTestId("tw-research-batch-failure-TW:2330")).toContainText("台積電");
  await expect(page.getByTestId("tw-research-batch-failure-TW:2331")).toContainText(
    "名稱不可用",
  );
});

test("ready for candidates once a snapshot exists", async ({ page }) => {
  setE2eTwResearchFirstPass(
    page,
    fixture({
      jobs: [
        baseJob({
          status: "completed",
          qualityStatus: "complete_attempted",
          activeSnapshotId: "fps_ready",
          cursor: 73,
        }),
      ],
      progress: baseProgress({
        status: "completed",
        qualityStatus: "complete_attempted",
        cursor: 73,
        processedCount: 73,
        pendingCount: 0,
        okCount: 65,
        partialCount: 5,
        failedCount: 3,
        activeSnapshotId: "fps_ready",
      }),
      researchRun: baseRun({
        phase: "awaiting_candidate_selection",
        status: "first_pass_ready",
        firstPassSnapshotId: "fps_ready",
      }),
    }),
  );
  await openResearch(page);
  await expect(page.getByTestId("tw-research-plain-status")).toHaveAttribute(
    "data-bucket",
    "ready_for_candidates",
  );
  await expect(page.getByTestId("tw-research-next-action")).toContainText(
    "儲存要深入分析的股票",
  );
});

test("English copy for paused_manual", async ({ page }) => {
  setE2eTwResearchFirstPass(
    page,
    fixture({
      progress: baseProgress({ leaseActive: false }),
    }),
  );
  await openResearch(page, { language: "en" });
  await expect(page.getByTestId("tw-research-plain-status")).toHaveAttribute(
    "data-bucket",
    "paused_manual",
  );
  await expect(page.getByTestId("tw-research-plain-status")).toContainText(
    "Paused: no collection is running",
  );
});

test("mobile viewport keeps investor summary readable", async ({ page }) => {
  setE2eTwResearchFirstPass(
    page,
    fixture({
      progress: baseProgress({ leaseActive: false }),
    }),
  );
  await openResearch(page, { viewport: { width: 390, height: 844 } });
  await expect(page.getByTestId("tw-research-investor-summary")).toBeVisible();
  await expect(page.getByTestId("tw-research-plain-status")).toHaveAttribute(
    "data-bucket",
    "paused_manual",
  );
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
});
