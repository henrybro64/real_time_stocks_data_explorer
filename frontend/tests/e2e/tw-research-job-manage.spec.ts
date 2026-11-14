import { setE2eAuthenticatedUser, setE2eTwResearchFirstPass } from "./e2eApiMocks";
import { expect, test } from "./setup";

async function openResearchWithJobs(
  page: import("@playwright/test").Page,
  jobs: Array<Record<string, unknown>>,
  extras: Parameters<typeof setE2eTwResearchFirstPass>[1] = {},
) {
  setE2eAuthenticatedUser(page, { id: "job-manage-user", username: "job-manage-user" });
  setE2eTwResearchFirstPass(page, {
    researchRun: {
      researchRunId: "rr_e2e",
      phase: "phase1_first_pass",
      status: "running",
      firstPassJobId: String(jobs[0]?.jobId ?? "fpj_e2e"),
    },
    jobs,
    progress: {
      jobId: String(jobs[0]?.jobId ?? "fpj_e2e"),
      status: String(jobs[0]?.status ?? "completed"),
      qualityStatus: "complete_attempted",
      universeSize: 1,
      cursor: 1,
      processedCount: 1,
      pendingCount: 0,
      okCount: 1,
      leaseActive: false,
    },
    ...extras,
  });
  await page.goto("/research/tw");
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
  await expect(page.getByTestId("tw-research-job-delete")).toBeVisible();
}

test("delete a pending job (200)", async ({ page }) => {
  await openResearchWithJobs(
    page,
    [
      {
        jobId: "fpj_pending",
        status: "pending",
        qualityStatus: "incomplete",
        createdAt: "2026-11-13T02:00:00Z",
      },
    ],
    {
      deleteJobResult: { deleted: true },
      deletionPreview: {
        previewToken: "tok",
        closure: { tw_research_first_pass_jobs: 1 },
        retained: [],
        blockers: [],
      },
    },
  );
  await page.getByTestId("tw-research-job-delete").click();
  await expect(page.getByTestId("tw-research-delete-modal")).toBeVisible();
  await page.getByTestId("tw-research-delete-modal-confirm").click();
  await expect(page.getByTestId("tw-research-first-pass-jobs-empty")).toBeVisible();
  await expect(page.getByTestId("tw-research-first-pass-job-history")).toHaveCount(0);
});

test("delete a running job (202 → 410)", async ({ page }) => {
  await openResearchWithJobs(
    page,
    [
      {
        jobId: "fpj_running",
        status: "running",
        qualityStatus: "incomplete",
        createdAt: "2026-11-13T02:00:00Z",
      },
    ],
    {
      deleteJobResult: { deletionState: "requested" },
      deletionStatusQueue: [
        { gone: false, deletionState: "requested", jobId: "fpj_running" },
        {
          gone: true,
          outcome: "deleted",
          counts: { tw_research_first_pass_jobs: 1 },
        },
      ],
      deletionPreview: {
        previewToken: "tok",
        closure: { tw_research_first_pass_jobs: 1 },
        retained: [],
        blockers: [],
      },
    },
  );
  await page.getByTestId("tw-research-job-delete").click();
  await page.getByTestId("tw-research-delete-modal-confirm").click();
  await expect(page.getByTestId("tw-research-job-manage-message")).toContainText(
    /Stopping|正在停止|已永久刪除|Permanently deleted/,
  );
});

test("full-run delete dialog", async ({ page }) => {
  await openResearchWithJobs(
    page,
    [
      {
        jobId: "fpj_full",
        status: "completed",
        qualityStatus: "complete_attempted",
        createdAt: "2026-11-13T02:00:00Z",
      },
    ],
    {
      runDeletionPreview: {
        previewToken: "run-tok",
        closure: {
          tw_research_research_runs: 1,
          tw_research_first_pass_jobs: 1,
        },
        retained: [],
        blockers: [],
      },
      deleteRunResult: { deleted: true },
    },
  );
  await page.getByTestId("tw-research-delete-full-run").click();
  await expect(page.getByTestId("tw-research-delete-modal")).toBeVisible();
  await expect(page.getByTestId("tw-research-delete-closure")).toBeVisible();
  await page.getByTestId("tw-research-delete-modal-confirm").click();
});

test("archive/restore with show-archived toggle", async ({ page }) => {
  await openResearchWithJobs(page, [
    {
      jobId: "fpj_arc",
      status: "completed",
      qualityStatus: "complete_attempted",
      createdAt: "2026-11-13T02:00:00Z",
    },
  ]);
  await page.getByTestId("tw-research-job-archive").click();
  await page.getByTestId("tw-research-confirm-modal-confirm").click();
  await page.getByTestId("tw-research-show-archived").check();
  await expect(page.getByTestId("tw-research-job-unarchive")).toBeVisible();
  await page.getByTestId("tw-research-job-unarchive").click();
});

test("job manage controls remain usable on mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openResearchWithJobs(page, [
    {
      jobId: "fpj_mobile",
      status: "completed",
      qualityStatus: "complete_attempted",
      createdAt: "2026-11-13T02:00:00Z",
    },
  ]);
  await expect(page.getByTestId("tw-research-job-delete")).toBeVisible();
  await expect(page.getByTestId("tw-research-show-archived")).toBeVisible();
});
