import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

import { LanguageProvider } from "../../app/i18n";
import { ApiError } from "../../services/api/client";
import {
  archiveTwResearchFirstPassJob,
  deleteTwResearchFirstPassJob,
  deleteTwResearchRunCurrent,
  fetchThemeOptions,
  fetchTwResearchFirstPassDeletionPreview,
  fetchTwResearchFirstPassDeletionStatus,
  fetchTwResearchFirstPassProgress,
  fetchTwResearchFirstPassSymbols,
  fetchTwResearchRunCurrent,
  fetchTwResearchRunDeletionPreview,
  fetchTwResearchRunDeletionStatus,
  fetchTwResearchScan,
  fetchTwResearchScanProgress,
  fetchTwResearchScans,
  listTwResearchFirstPassJobs,
  unarchiveTwResearchFirstPassJob,
} from "../../services/api/twResearch";
import { TwResearchPage } from "./TwResearchPage";

vi.mock("../../services/api/twResearch", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/twResearch")>(
    "../../services/api/twResearch",
  );
  return {
    ...actual,
    fetchThemeOptions: vi.fn(async () => []),
    fetchTwResearchScans: vi.fn(async () => ({ scans: [] })),
    fetchTwResearchScan: vi.fn(),
    fetchTwResearchScanProgress: vi.fn(),
    fetchTwResearchLatestManifest: vi.fn(async () => ({})),
    fetchTwResearchMembership: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    fetchTwResearchLatestCandidateSet: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    fetchTwResearchRunCurrent: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    fetchTwResearchReportEvidenceExampleZhTw: vi.fn(async () => ({})),
    listTwResearchFirstPassJobs: vi.fn(async () => []),
    fetchTwResearchFirstPassProgress: vi.fn(async () => ({
      jobId: "fpj_manage",
      status: "completed",
      qualityStatus: "complete_attempted",
      universeSize: 1,
      cursor: 1,
      processedCount: 1,
      pendingCount: 0,
      okCount: 1,
      partialCount: 0,
      failedCount: 0,
      deferredCount: 0,
      leaseActive: false,
    })),
    fetchTwResearchFirstPassSymbols: vi.fn(async () => ({
      jobId: "fpj_manage",
      records: [],
      total: 0,
    })),
    fetchTwResearchFirstPassDeletionPreview: vi.fn(),
    fetchTwResearchFirstPassDeletionStatus: vi.fn(),
    fetchTwResearchRunDeletionPreview: vi.fn(),
    fetchTwResearchRunDeletionStatus: vi.fn(),
    deleteTwResearchFirstPassJob: vi.fn(),
    deleteTwResearchRunCurrent: vi.fn(),
    archiveTwResearchFirstPassJob: vi.fn(),
    unarchiveTwResearchFirstPassJob: vi.fn(),
    cancelTwResearchFirstPassJob: vi.fn(),
  };
});

const JOB_ID = "fpj_manage";
const PENDING_JOB_KEY = "tw-research-pending-deletion-job";

function jobRecord(overrides: Record<string, unknown> = {}) {
  return {
    jobId: JOB_ID,
    status: "completed",
    qualityStatus: "complete_attempted",
    createdAt: "2026-11-13T02:00:00Z",
    updatedAt: "2026-11-13T03:50:00Z",
    activeSnapshotId: "fps_1",
    ...overrides,
  };
}

function renderPage(language: "zh-TW" | "en" = "zh-TW") {
  window.localStorage.setItem("stock-ticker-language", language);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <TwResearchPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("TwResearchJobManage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([jobRecord()]);
    vi.mocked(fetchTwResearchFirstPassDeletionPreview).mockResolvedValue({
      previewToken: "tok-job",
      closure: {
        tw_research_first_pass_jobs: 1,
        tw_research_first_pass_symbol_results: 2,
      },
      retained: [{ code: "first_pass_snapshot" }],
      blockers: [],
    });
    vi.mocked(fetchTwResearchRunDeletionPreview).mockResolvedValue({
      previewToken: "tok-run",
      closure: { tw_research_research_runs: 1, tw_research_first_pass_jobs: 1 },
      retained: [],
      blockers: [],
    });
  });

  afterEach(() => {
    cleanup();
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  it("delete preview modal shows closure counts and retained items", async () => {
    const user = userEvent.setup();
    renderPage("en");
    await screen.findByTestId("tw-research-job-delete");
    await user.click(screen.getByTestId("tw-research-job-delete"));
    const modal = await screen.findByTestId("tw-research-delete-modal");
    expect(within(modal).getByTestId("tw-research-delete-closure")).toBeInTheDocument();
    expect(modal.textContent).toMatch(/shared first-round data|共用的第一輪資料/);
  });

  it("200 delete removes the job and clears selection", async () => {
    const user = userEvent.setup();
    let deleted = false;
    vi.mocked(deleteTwResearchFirstPassJob).mockImplementation(async () => {
      deleted = true;
      return { deleted: true };
    });
    vi.mocked(listTwResearchFirstPassJobs).mockImplementation(async () =>
      deleted ? [] : [jobRecord()],
    );
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-job-delete"));
    await user.click(await screen.findByTestId("tw-research-delete-modal-confirm"));
    await waitFor(() => {
      expect(deleteTwResearchFirstPassJob).toHaveBeenCalledWith(JOB_ID, "tok-job");
    });
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-first-pass-jobs-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("tw-research-first-pass-job-history")).not.toBeInTheDocument();
  });

  it("202 then 410 gone shows permanent delete copy and clears selection", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTimeAsync });
    vi.mocked(deleteTwResearchFirstPassJob).mockResolvedValue({
      deletionState: "requested",
    });
    let statusCalls = 0;
    vi.mocked(fetchTwResearchFirstPassDeletionStatus).mockImplementation(async () => {
      statusCalls += 1;
      if (statusCalls < 2) {
        return { gone: false, deletionState: "requested", jobId: JOB_ID };
      }
      return {
        gone: true,
        outcome: "deleted",
        counts: { tw_research_first_pass_jobs: 1 },
      };
    });
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([
      jobRecord({ status: "running" }),
    ]);
    renderPage("zh-TW");
    await user.click(await screen.findByTestId("tw-research-job-delete"));
    await user.click(await screen.findByTestId("tw-research-delete-modal-confirm"));
    await waitFor(() => {
      expect(fetchTwResearchFirstPassDeletionStatus).toHaveBeenCalled();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_500);
    });
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-job-manage-message").textContent).toMatch(
        /已永久刪除/,
      );
    });
    vi.useRealTimers();
  });

  it("failed deletion offers retry", async () => {
    const user = userEvent.setup();
    vi.mocked(deleteTwResearchFirstPassJob).mockResolvedValue({
      deletionState: "requested",
    });
    vi.mocked(fetchTwResearchFirstPassDeletionStatus).mockResolvedValue({
      gone: false,
      deletionState: "failed",
      deletionError: "RuntimeError",
    });
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-job-delete"));
    await user.click(await screen.findByTestId("tw-research-delete-modal-confirm"));
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-retry-delete")).toBeInTheDocument();
    });
  });

  it("blocked 409 shows reason and keeps job selected", async () => {
    const user = userEvent.setup();
    vi.mocked(deleteTwResearchFirstPassJob).mockRejectedValue(
      new ApiError("Request failed: 409 - snapshot_shared_with_other_job", {
        status: 409,
        detail: "snapshot_shared_with_other_job",
        kind: "validation",
      }),
    );
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-job-delete"));
    await user.click(await screen.findByTestId("tw-research-delete-modal-confirm"));
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-job-manage-message").textContent).toMatch(
        /shared|共用/,
      );
    });
    expect(screen.getByTestId("tw-research-job-delete")).toBeInTheDocument();
  });

  it("full-run delete flow requests and polls status", async () => {
    const user = userEvent.setup();
    vi.mocked(deleteTwResearchRunCurrent).mockResolvedValue({
      deletionState: "requested",
    });
    vi.mocked(fetchTwResearchRunDeletionStatus).mockResolvedValue({
      gone: true,
      outcome: "deleted",
      counts: { tw_research_research_runs: 1 },
    });
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-delete-full-run"));
    await user.click(await screen.findByTestId("tw-research-delete-modal-confirm"));
    await waitFor(() => {
      expect(deleteTwResearchRunCurrent).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(fetchTwResearchRunDeletionStatus).toHaveBeenCalled();
    });
  });

  it("archive hides job until show-archived, then restore", async () => {
    const user = userEvent.setup();
    vi.mocked(archiveTwResearchFirstPassJob).mockResolvedValue(
      jobRecord({ archivedAt: "2026-11-14T01:00:00Z" }),
    );
    vi.mocked(unarchiveTwResearchFirstPassJob).mockResolvedValue(
      jobRecord({ archivedAt: null }),
    );
    vi.mocked(listTwResearchFirstPassJobs)
      .mockResolvedValueOnce([jobRecord()])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([jobRecord({ archivedAt: "2026-11-14T01:00:00Z" })])
      .mockResolvedValue([jobRecord()]);
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-job-archive"));
    await user.click(await screen.findByTestId("tw-research-confirm-modal-confirm"));
    await waitFor(() => {
      expect(archiveTwResearchFirstPassJob).toHaveBeenCalled();
    });
    const toggle = screen.getByTestId("tw-research-show-archived");
    await user.click(toggle);
    await waitFor(() => {
      expect(listTwResearchFirstPassJobs).toHaveBeenCalledWith(20, true);
    });
  });

  it("deletion-state labels appear in the job list", async () => {
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([
      jobRecord({ deletionState: "requested", status: "cancelled" }),
    ]);
    renderPage("en");
    const history = await screen.findByTestId("tw-research-first-pass-job-history");
    expect(history.textContent).toMatch(/Stopping and deleting|正在停止並刪除/);
  });

  it("resumes pending deletion polling from sessionStorage after remount", async () => {
    sessionStorage.setItem(PENDING_JOB_KEY, JOB_ID);
    vi.mocked(fetchTwResearchFirstPassDeletionStatus).mockResolvedValue({
      gone: true,
      outcome: "deleted",
      counts: { tw_research_first_pass_jobs: 1 },
    });
    renderPage("zh-TW");
    await waitFor(() => {
      expect(fetchTwResearchFirstPassDeletionStatus).toHaveBeenCalledWith(JOB_ID);
    });
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-job-manage-message").textContent).toMatch(
        /已永久刪除/,
      );
    });
  });
});
