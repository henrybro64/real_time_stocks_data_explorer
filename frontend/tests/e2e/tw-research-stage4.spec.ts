import {
  setE2eAuthenticatedUser,
  setE2eTwResearchFirstPass,
  type E2eTwResearchFirstPassFixture,
} from "./e2eApiMocks";
import { expect, test } from "./setup";

/**
 * #361 Stage-4 ordinary path (mocked APIs, no live providers).
 */

const JOB_ID = "fpj_e2e_stage4";
const RUN_ID = "rr_e2e_stage4";
const SCAN_ID = "scan_e2e_stage4";
const DIGEST = "a".repeat(64);

function baseFixture(): E2eTwResearchFirstPassFixture {
  return {
    researchRun: null,
    jobs: [],
    progress: null,
    candidateSet: null,
    phase2Execution: null,
    technicalResults: null,
    technicalScan: null,
  };
}

async function openResearch(
  page: import("@playwright/test").Page,
  options?: { viewport?: { width: number; height: number }; language?: "zh-TW" | "en" },
) {
  setE2eAuthenticatedUser(page, { id: "stage4-user", username: "stage4-user" });
  if (options?.viewport) {
    await page.setViewportSize(options.viewport);
  }
  await page.addInitScript((lang) => {
    window.localStorage.setItem("stock-ticker-language", lang);
  }, options?.language ?? "zh-TW");
  await page.goto("/research/tw");
  await expect(page.getByTestId("tw-research-page")).toBeVisible();
}

function assertLegacyControlsAbsent(page: import("@playwright/test").Page) {
  return Promise.all([
    expect(page.getByTestId("tw-research-create")).toHaveCount(0),
    expect(page.getByTestId("tw-research-batch")).toHaveCount(0),
    expect(page.getByTestId("tw-research-history")).toHaveCount(0),
  ]);
}

test.describe("TW research Stage-4 ordinary path (#361)", () => {
  test("Start → collecting → awaiting → approve → technical → results; reload keeps state", async ({
    page,
  }) => {
    const fixture = baseFixture();
    setE2eTwResearchFirstPass(page, fixture);
    await openResearch(page);
    await assertLegacyControlsAbsent(page);

    // Start Research → collecting
    fixture.jobs = [
      {
        jobId: JOB_ID,
        status: "running",
        qualityStatus: "incomplete",
        activeSnapshotId: null,
        universeSize: 3,
        createdAt: "2024-06-28T00:00:00Z",
      },
    ];
    fixture.progress = {
      jobId: JOB_ID,
      status: "running",
      universeSize: 3,
      cursor: 1,
      processedCount: 1,
      pendingCount: 2,
      okCount: 1,
    };
    fixture.researchRun = {
      researchRunId: RUN_ID,
      userId: "stage4-user",
      phase: "phase1_first_pass",
      status: "running",
      firstPassJobId: JOB_ID,
      scheduleEnabled: true,
      execution: { state: "actively_collecting" },
    };
    await page.getByTestId("tw-research-start-full-research").click();
    await expect(page.getByTestId("tw-research-execution-status")).toBeVisible({
      timeout: 15_000,
    });
    await page.reload();
    await expect(page.getByTestId("tw-research-page")).toBeVisible();
    await expect(page.getByTestId("tw-research-execution-status")).toBeVisible();

    // Collecting → awaiting candidates
    fixture.jobs = [
      {
        jobId: JOB_ID,
        status: "completed",
        qualityStatus: "complete_attempted",
        activeSnapshotId: "fps_stage4",
        universeSize: 3,
      },
    ];
    fixture.progress = {
      jobId: JOB_ID,
      status: "completed",
      universeSize: 3,
      cursor: 3,
      processedCount: 3,
      pendingCount: 0,
      okCount: 3,
      activeSnapshotId: "fps_stage4",
    };
    fixture.candidateSet = {
      candidateSetId: "cs_stage4",
      version: 1,
      firstPassSnapshotId: "fps_stage4",
      firstPassDigest: DIGEST,
      stockIds: ["TW:2330"],
      origin: "manual",
    };
    fixture.researchRun = {
      researchRunId: RUN_ID,
      userId: "stage4-user",
      phase: "awaiting_analyst_candidate_selection",
      status: "gated",
      firstPassJobId: JOB_ID,
      firstPassSnapshotId: "fps_stage4",
      gates: { candidateSelection: "pending" },
      scheduleEnabled: false,
    };
    await page.reload();
    await expect(page.getByTestId("tw-research-approve-candidates")).toBeEnabled({
      timeout: 15_000,
    });
    await assertLegacyControlsAbsent(page);

    // Approve → awaiting technical start
    await page.getByTestId("tw-research-approve-candidates").click();
    await expect(page.getByTestId("tw-research-start-technical-from-candidates")).toBeEnabled({
      timeout: 15_000,
    });
    await page.reload();
    await expect(page.getByTestId("tw-research-start-technical-from-candidates")).toBeEnabled();

    // Start technical → analyzing with partial results
    await page.getByTestId("tw-research-start-technical-from-candidates").click();
    await expect(page.getByTestId("tw-research-phase2-execution-status")).toContainText(
      /分析中|Analyzing/,
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("tw-research-technical-results")).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("tw-research-phase2-execution-status")).toContainText(
      /分析中|Analyzing/,
    );

    // Analyzing → completed results with numbers
    fixture.phase2Execution = { state: "completed", technicalScanId: SCAN_ID };
    fixture.researchRun = {
      ...fixture.researchRun!,
      phase: "complete_evidence_ready",
      status: "evidence_ready",
      technicalScanId: SCAN_ID,
      gates: { candidateSelection: "approved" },
    };
    fixture.technicalScan = {
      scanId: SCAN_ID,
      tradingDay: "2024-06-28",
      status: "verified_ready",
      cursor: 1,
      universeSize: 1,
      processedCount: 1,
      remainingCount: 0,
      ledger: [],
      stockIds: ["TW:2330"],
    };
    fixture.technicalResults = {
      technicalScanId: SCAN_ID,
      scanStatus: "verified_ready",
      calculationVersion: "macd12-26-9_kd9-3-3_monthly_first_v1",
      priceBasis: "unadjusted_close",
      analyzedCount: 1,
      insufficientHistoryCount: 0,
      phase2Execution: { state: "completed" },
      candidates: [
        {
          stockId: "TW:2330",
          name: "台積電",
          overallStageResult: "pass",
          monthly: {
            asOf: "2024-06",
            dif: 1.23,
            signal: 0.98,
            osc: 0.25,
            k: 72.16,
            d: 65.4,
            barCount: 48,
            quality: "ok",
            passStatus: "pass",
          },
          daily: {
            asOf: "2024-06-28",
            dif: 2.5,
            signal: 1.1,
            osc: 1.4,
            k: 80,
            d: 70,
            barCount: 120,
            quality: "ok",
            passStatus: "pass",
          },
        },
      ],
    };
    await page.reload();
    await expect(page.getByTestId("tw-research-technical-results")).toBeVisible();
    await expect(page.getByTestId("tw-research-technical-results")).toContainText("1.23");
    await expect(page.getByTestId("tw-research-technical-results")).toContainText("72.16");
    await assertLegacyControlsAbsent(page);
  });

  test("mobile viewport shows stacked results cards", async ({ page }) => {
    const fixture = baseFixture();
    fixture.researchRun = {
      researchRunId: RUN_ID,
      userId: "stage4-user",
      phase: "complete_evidence_ready",
      status: "evidence_ready",
      technicalScanId: SCAN_ID,
      gates: { candidateSelection: "approved" },
    };
    fixture.phase2Execution = { state: "completed", technicalScanId: SCAN_ID };
    fixture.jobs = [
      {
        jobId: JOB_ID,
        status: "completed",
        qualityStatus: "complete_attempted",
        activeSnapshotId: "fps_stage4",
      },
    ];
    fixture.candidateSet = {
      candidateSetId: "cs_stage4",
      version: 2,
      firstPassSnapshotId: "fps_stage4",
      firstPassDigest: DIGEST,
      stockIds: ["TW:2330"],
    };
    fixture.technicalResults = {
      technicalScanId: SCAN_ID,
      scanStatus: "verified_ready",
      candidates: [
        {
          stockId: "TW:2330",
          name: "台積電",
          overallStageResult: "pass",
          monthly: {
            asOf: "2024-06",
            dif: 1.23,
            signal: 0.98,
            osc: 0.25,
            k: 72.16,
            d: 65.4,
            barCount: 48,
            quality: "ok",
            passStatus: "pass",
          },
          daily: {
            asOf: "2024-06-28",
            dif: 2.5,
            signal: 1.1,
            osc: 1.4,
            k: 80,
            d: 70,
            barCount: 120,
            quality: "ok",
            passStatus: "pass",
          },
        },
      ],
    };
    setE2eTwResearchFirstPass(page, fixture);
    await openResearch(page, { viewport: { width: 390, height: 844 } });
    await expect(page.getByTestId("tw-research-technical-results-cards")).toBeVisible();
    await expect(page.getByTestId("tw-research-technical-result-card")).toContainText("1.23");
    await assertLegacyControlsAbsent(page);
  });
});
