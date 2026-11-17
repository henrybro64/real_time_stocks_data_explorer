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
  advanceTwResearchRunGate,
  fetchThemeOptions,
  fetchTwResearchFirstPassJob,
  fetchTwResearchFirstPassProgress,
  fetchTwResearchFirstPassSymbols,
  fetchTwResearchRunCurrent,
  fetchTwResearchScan,
  fetchTwResearchScanProgress,
  fetchTwResearchScans,
  adoptTwResearchFirstPassJob,
  listTwResearchFirstPassJobs,
  researchProgressRevision,
  runTwResearchFirstPassBatch,
  startTwResearchRun,
  type TwResearchRun,
  type TwResearchScanProgress,
  type TwResearchScanSnapshot,
} from "../../services/api/twResearch";
import { TwResearchPage } from "./TwResearchPage";

vi.mock("../../services/api/twResearch", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/twResearch")>(
    "../../services/api/twResearch",
  );
  return {
    ...actual,
    fetchThemeOptions: vi.fn(),
    fetchTwResearchScans: vi.fn(async () => ({ scans: [] })),
    fetchTwResearchScan: vi.fn(),
    fetchTwResearchScanProgress: vi.fn(),
    fetchTwResearchTechnicalResults: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    fetchTwResearchMembership: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    fetchTwResearchLatestCandidateSet: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    fetchTwResearchRunCurrent: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    listTwResearchFirstPassJobs: vi.fn(async () => []),
    fetchTwResearchFirstPassJob: vi.fn(async () => {
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    }),
    fetchTwResearchFirstPassProgress: vi.fn(),
    fetchTwResearchFirstPassSymbols: vi.fn(),
    runTwResearchFirstPassBatch: vi.fn(),
    advanceTwResearchRunGate: vi.fn(async () => ({})),
    startTwResearchRun: vi.fn(),
    adoptTwResearchFirstPassJob: vi.fn(),
    fetchTwResearchFirstPassDeletionPreview: vi.fn(async () => ({
      previewToken: "tok",
      closure: { tw_research_first_pass_jobs: 1 },
      retained: [],
      blockers: [],
    })),
    fetchTwResearchRunDeletionPreview: vi.fn(async () => ({
      previewToken: "tok-run",
      closure: { tw_research_research_runs: 1 },
      retained: [],
      blockers: [],
    })),
    archiveTwResearchFirstPassJob: vi.fn(async () => ({})),
    cancelTwResearchFirstPassJob: vi.fn(async () => ({})),
  };
});

const JOB_ID = "fpj_demo";
const RUN_ID = "rr_demo";
const SCAN_ID = "scan_demo_completed_processing";
const FORBIDDEN =
  /#\d{3}|Notion|CronJob|GitHub|\blease\b|\bworker\b|fpj_|rr_|offset|enrichment|\buniverse\b|snapshot|宇宙|候選集|快照|游標|\bJSON\b/i;
const SNAKE_CASE = /\b[a-z]+_[a-z_]+\b/;

function jobRecord(overrides: Record<string, unknown> = {}) {
  return {
    jobId: JOB_ID,
    status: "running",
    qualityStatus: "incomplete",
    createdAt: "2026-11-13T02:00:00Z",
    updatedAt: "2026-11-13T03:50:00Z",
    activeSnapshotId: null,
    ...overrides,
  };
}

function progressRecord(overrides: Record<string, unknown> = {}) {
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

function runRecord(overrides: Partial<TwResearchRun> = {}): TwResearchRun {
  return {
    researchRunId: RUN_ID,
    userId: "u1",
    phase: "phase1_first_pass",
    status: "running",
    firstPassJobId: JOB_ID,
    createdAt: "2026-11-13T02:00:00Z",
    scheduleEnabled: false,
    firstPassDrain: { armed: false, available: false },
    ...overrides,
  };
}

function seed(options: {
  job?: Record<string, unknown>;
  jobs?: Array<Record<string, unknown>>;
  progress?: Record<string, unknown>;
  run?: Partial<TwResearchRun> | null;
}) {
  const listed =
    options.jobs ??
    (options.job === undefined && options.jobs === undefined
      ? [jobRecord()]
      : options.job
        ? [jobRecord(options.job)]
        : []);
  vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue(listed);
  vi.mocked(fetchTwResearchFirstPassProgress).mockResolvedValue(
    progressRecord(options.progress),
  );
  if (options.run === null) {
    vi.mocked(fetchTwResearchRunCurrent).mockRejectedValue(
      new ApiError("missing", { status: 404, detail: "x", kind: "not_found" }),
    );
  } else {
    vi.mocked(fetchTwResearchRunCurrent).mockResolvedValue(runRecord(options.run));
  }
}

function renderPage(
  language: "zh-TW" | "en" = "zh-TW",
  options?: { initialScanId?: string | null },
) {
  window.localStorage.setItem("stock-ticker-language", language);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <TwResearchPage initialScanId={options?.initialScanId ?? null} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

function technicalScanFixture(): {
  scan: TwResearchScanSnapshot;
  progress: TwResearchScanProgress;
} {
  const base = {
    scanId: SCAN_ID,
    tradingDay: "2026-11-13",
    universeVersion: "tw-starter",
    status: "completed_processing",
    qualityStatus: "processed",
    stageOrder: "monthly_first" as const,
    cursor: 73,
    enrichCursor: 0,
    universeSize: 73,
    processedCount: 73,
    remainingCount: 0,
    targetCount: 10,
    dayPassCount: 8,
    monthPassCount: 12,
    dailyDeferredCount: 2,
    qualifiedCount: 8,
    enrichmentsRun: 0,
    maxEnrichmentsPerRun: 20,
    upstreamMonthFetches: 10,
    maxUpstreamMonthFetches: 40,
    updatedAt: "2026-11-13T08:28:52+08:00",
    activeSnapshotId: null,
    workerActivity: "idle" as const,
    notes: [] as string[],
    ledger: [] as Array<Record<string, unknown>>,
    checkpointsEphemeral: true,
  };
  return {
    scan: base,
    progress: {
      ...base,
      includesLedger: false,
      ledgerRevision: researchProgressRevision({
        updatedAt: base.updatedAt,
        status: base.status,
        qualityStatus: base.qualityStatus,
        cursor: base.cursor,
        enrichCursor: base.enrichCursor,
        processedCount: base.processedCount,
      }),
    },
  };
}

const status = () => screen.getByTestId("tw-research-plain-status");

async function waitForBucket(bucket: string) {
  await waitFor(() => {
    expect(screen.getByTestId("tw-research-plain-status")).toHaveAttribute(
      "data-bucket",
      bucket,
    );
  });
}

beforeEach(() => {
  vi.mocked(fetchThemeOptions).mockResolvedValue([]);
  vi.mocked(fetchTwResearchScans).mockResolvedValue({ scans: [] });
  vi.mocked(fetchTwResearchRunCurrent).mockClear();
  vi.mocked(fetchTwResearchFirstPassProgress).mockClear();
  vi.mocked(listTwResearchFirstPassJobs).mockClear();
  vi.mocked(fetchTwResearchFirstPassSymbols).mockReset();
  vi.mocked(runTwResearchFirstPassBatch).mockReset();
  vi.mocked(advanceTwResearchRunGate).mockResolvedValue({} as never);
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.useRealTimers();
});

describe("TwResearchPage investor status scenarios (#350/#351)", () => {
  it("empty / first-time user: idle status and start action", async () => {
    seed({ run: null });
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(listTwResearchFirstPassJobs).toHaveBeenCalled());
    expect(status()).toHaveAttribute("data-bucket", "idle");
    expect(screen.getByTestId("tw-research-next-action")).toHaveTextContent(
      "下一步：按下「開始研究」",
    );
    expect(screen.getByRole("button", { name: "開始研究" })).toBeEnabled();
  });

  it("0/73 not started", async () => {
    seed({
      job: { status: "pending" },
      progress: {
        status: "pending",
        cursor: 0,
        processedCount: 0,
        pendingCount: 73,
        okCount: 0,
      },
    });
    renderPage();
    await waitForBucket("not_started");
    expect(screen.getByTestId("tw-research-investor-counts")).toHaveTextContent(
      "研究股票 73 · 已處理 0",
    );
    expect(screen.getByTestId("tw-research-next-action")).toHaveTextContent(
      "目前沒有自動收集",
    );
  });

  it("collecting_now at 20/73 when the batch lease is active", async () => {
    seed({ progress: { leaseActive: true } });
    renderPage();
    await waitForBucket("collecting_now");
    expect(status()).toHaveTextContent("正在收集資料（本批進行中）");
    expect(screen.getByTestId("tw-research-investor-counts")).toHaveTextContent(
      "已處理 20",
    );
    expect(screen.getByTestId("tw-research-next-action")).toHaveTextContent(
      "重新整理頁面不會中斷進度",
    );
  });

  it("paused_manual: running, cursor>0, no lease, drain off", async () => {
    seed({ progress: { leaseActive: false, cursor: 20 } });
    renderPage();
    await waitForBucket("paused_manual");
    expect(status()).toHaveTextContent("目前暫停：沒有正在執行的收集");
    expect(status()).not.toHaveTextContent("正在收集");
  });

  it("paused_auto with the drain's last run in Taipei time", async () => {
    seed({
      run: {
        firstPassDrain: {
          armed: true,
          available: true,
          lastTickAt: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
        },
      },
    });
    renderPage();
    await waitForBucket("paused_auto");
    expect(status().textContent).toMatch(
      /^等待下一次自動收集（上次執行 \d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}）$/,
    );
  });

  it("paused_auto_stale when the drain is armed but has no recent tick", async () => {
    seed({
      run: { firstPassDrain: { armed: true, available: true, lastTickAt: null } },
    });
    renderPage("en");
    await waitForBucket("paused_auto_stale");
    expect(status()).toHaveTextContent(
      "Automatic collection is on, but there is no recent run record",
    );
  });

  it("waiting_retry renders next retry and updatedAt as Taipei times, not ISO", async () => {
    seed({
      job: { status: "deferred" },
      progress: {
        status: "deferred",
        failedCount: 4,
        nextRetryAt: "2026-11-13T04:30:00Z",
        updatedAt: "2026-11-13T03:50:00Z",
      },
    });
    renderPage();
    await waitForBucket("waiting_retry");
    expect(screen.getByTestId("tw-research-next-retry")).toHaveTextContent(
      "2026/11/13 12:30",
    );
    expect(screen.getByTestId("tw-research-progress-updated")).toHaveTextContent(
      "2026/11/13 11:50",
    );
    const summary = screen.getByTestId("tw-research-investor-summary");
    expect(summary.textContent).not.toMatch(/2026-11-13T/);
    expect(screen.getByTestId("tw-research-failures-so-far")).toHaveTextContent(
      "目前 4 檔失敗，已排定重試（2026/11/13 12:30）",
    );
  });

  it("falls back to the run's nested progress for nextRetryAt / lastError", async () => {
    seed({
      progress: { nextRetryAt: null, failedCount: 2 },
      run: {
        firstPassProgress: {
          nextRetryAt: "2026-11-13T05:00:00Z",
          lastError: "timeout",
        },
      },
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("tw-research-next-retry")).toHaveTextContent(
        "2026/11/13 13:00",
      ),
    );
  });

  it("10/10 failed batch (HTTP 200) shows the per-batch banner with names and reasons", async () => {
    const user = userEvent.setup();
    const batchIds = Array.from({ length: 10 }, (_, i) => `TW:${2330 + i}`);
    seed({
      progress: {
        // Cumulative state: nothing failed yet according to the cached progress.
        okCount: 20,
        failedCount: 0,
        processedCount: 20,
      },
    });
    vi.mocked(runTwResearchFirstPassBatch).mockResolvedValue({
      jobId: JOB_ID,
      status: "running",
      activeSnapshotId: null,
      batchIds,
      batchCounts: { ok: 0, partial: 0, failed: 10 },
      processed: 10,
    });
    vi.mocked(fetchTwResearchFirstPassSymbols).mockResolvedValue({
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
        {
          stockId: "TW:2332",
          stockName: "友訊",
          nameStatus: "ok",
          evidence: { stockName: "友訊", failureClass: "programming_error" },
        },
        {
          stockId: "TW:2333",
          stockName: "建舜電",
          nameStatus: "ok",
          evidence: { stockName: "建舜電", failureClass: "permanent" },
        },
        // Failed in an earlier batch — must not appear in "this batch".
        {
          stockId: "TW:1101",
          stockName: "台泥",
          nameStatus: "ok",
          evidence: { stockName: "台泥", failureClass: "transient" },
        },
      ],
    });
    renderPage();
    await waitForBucket("paused_manual");

    await user.click(await screen.findByTestId("tw-research-run-first-pass-batch"));

    expect(await screen.findByTestId("tw-research-batch-all-failed")).toHaveTextContent(
      "本批 10 檔資料收集失敗，尚無可驗證資料",
    );
    expect(fetchTwResearchFirstPassSymbols).toHaveBeenCalledWith(JOB_ID, {
      status: "failed",
      stockIds: batchIds,
      limit: 10,
    });
    expect(screen.getByTestId("tw-research-batch-failure-TW:2330")).toHaveTextContent(
      "台積電 (2330) — 暫時無法取得，將自動重試",
    );
    expect(screen.getByTestId("tw-research-batch-failure-TW:2331")).toHaveTextContent(
      "名稱不可用 (2331) — 資料來源限流，稍後重試",
    );
    expect(screen.getByTestId("tw-research-batch-failure-TW:2332")).toHaveTextContent(
      "友訊 (2332) — 系統錯誤，已停止並需要修復",
    );
    expect(screen.getByTestId("tw-research-batch-failure-TW:2333")).toHaveTextContent(
      "建舜電 (2333) — 資料來源沒有此資料",
    );
    expect(
      screen.queryByTestId("tw-research-batch-failure-TW:1101"),
    ).not.toBeInTheDocument();
    // Cumulative status stays honest: still unfinished/paused, not "all failed".
    expect(status()).not.toHaveTextContent("沒有可用的驗證資料");
  });

  it("DB unavailable on refetch with cached data shows the stale banner, not collecting_now", async () => {
    seed({ progress: { leaseActive: true } });
    const { client } = renderPage();
    await waitForBucket("collecting_now");

    vi.mocked(fetchTwResearchFirstPassProgress).mockRejectedValue(
      new ApiError("Request failed: 503", {
        status: 503,
        detail: JSON.stringify({ error: "first_pass_job_store_unavailable" }),
        kind: "unavailable",
        requestId: "req-503",
      }),
    );
    await act(async () => {
      await client.refetchQueries({
        queryKey: ["tw-research-first-pass-progress", JOB_ID],
      });
    });

    const banner = await screen.findByTestId("tw-research-stale-banner");
    expect(banner.textContent).toMatch(/以下為最後已知狀態（.+），可能已過時/);
    expect(screen.getByTestId("tw-research-plain-status")).not.toHaveAttribute(
      "data-bucket",
      "collecting_now",
    );
    expect(status()).not.toHaveTextContent("正在收集資料");

    // Plain-language error with raw detail + request id only in advanced.
    const errorBlock = screen.getByTestId("tw-research-error-first-pass-progress");
    expect(errorBlock).toHaveTextContent("資料庫暫時無法使用");
    const raw = within(errorBlock).getByTestId("tw-research-error-first-pass-progress-raw");
    expect(raw).toHaveTextContent("first_pass_job_store_unavailable");
    expect(raw).toHaveTextContent("request req-503");
    expect(raw.closest('[data-testid="tw-research-advanced-details"]')).not.toBeNull();
  });

  it("stale banner in English when the research-run refetch fails", async () => {
    seed({ progress: { leaseActive: true } });
    const { client } = renderPage("en");
    await waitForBucket("collecting_now");
    vi.mocked(fetchTwResearchRunCurrent).mockRejectedValue(
      new ApiError("Request failed: 504", { status: 504, detail: null, kind: "unknown" }),
    );
    await act(async () => {
      await client.refetchQueries({ queryKey: ["tw-research-run-current"] });
    });
    expect((await screen.findByTestId("tw-research-stale-banner")).textContent).toMatch(
      /^Last known state \(.+\); may be out of date$/,
    );
    expect(screen.getByTestId("tw-research-plain-status")).not.toHaveAttribute(
      "data-bucket",
      "collecting_now",
    );
    expect(screen.getByTestId("tw-research-error-research-run")).toHaveTextContent(
      "The request timed out or the network dropped",
    );
  });

  it("73/73 with gaps is not described as complete", async () => {
    seed({
      job: { status: "completed", activeSnapshotId: null },
      progress: {
        status: "completed",
        cursor: 73,
        processedCount: 73,
        pendingCount: 0,
        okCount: 60,
        partialCount: 5,
        failedCount: 8,
        activeSnapshotId: null,
      },
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("tw-research-investor-counts")).toHaveTextContent(
        "已處理 73",
      ),
    );
    expect(screen.getByTestId("tw-research-investor-counts")).toHaveTextContent("失敗 8");
    expect(status()).not.toHaveAttribute("data-bucket", "ready_for_candidates");
    expect(status()).not.toHaveAttribute("data-bucket", "collecting_now");
  });

  it("ready for candidates once a snapshot exists", async () => {
    seed({
      job: { status: "completed", activeSnapshotId: "fps_1" },
      progress: {
        status: "completed",
        cursor: 73,
        processedCount: 73,
        pendingCount: 0,
        okCount: 65,
        partialCount: 5,
        failedCount: 3,
        activeSnapshotId: "fps_1",
      },
    });
    renderPage();
    await waitForBucket("ready_for_candidates");
    expect(screen.getByTestId("tw-research-next-action")).toHaveTextContent(
      "儲存要深入分析的股票",
    );
  });

  it("reload restores the same state from durable data", async () => {
    seed({ progress: { leaseActive: true } });
    const first = renderPage();
    await waitForBucket("collecting_now");
    first.unmount();
    renderPage();
    await waitForBucket("collecting_now");
    expect(screen.getByTestId("tw-research-investor-counts")).toHaveTextContent(
      "已處理 20",
    );
  });
});

describe("TwResearchPage default UI hides internal terms (#351)", () => {
  async function assertPlainDefaultUi(language: "zh-TW" | "en") {
    seed({
      job: { status: "running" },
      progress: {
        leaseActive: false,
        failedCount: 3,
        nextRetryAt: "2026-11-13T04:30:00Z",
      },
      run: {
        firstPassDrain: {
          armed: true,
          available: true,
          lastTickAt: new Date(Date.now() - 60 * 1000).toISOString(),
        },
      },
    });
    const { container } = renderPage(language);
    await waitForBucket("paused_auto");

    const clone = document.body.cloneNode(true) as HTMLElement;
    clone
      .querySelectorAll('[data-testid="tw-research-advanced-details"]')
      .forEach((node) => node.remove());
    // Hidden by design: the page's raw JSON text areas live under Advanced.
    const visibleText = clone.textContent ?? "";
    expect(visibleText).not.toMatch(FORBIDDEN);

    // The ids still exist, but only inside the Advanced disclosure.
    const advanced = Array.from(
      container.querySelectorAll('[data-testid="tw-research-advanced-details"]'),
    )
      .map((node) => node.textContent ?? "")
      .join(" ");
    expect(advanced).toMatch(/fpj_demo/);
    expect(advanced).toMatch(/rr_demo/);

    // One primary Start/Resume CTA: active run shows View progress (#359).
    expect(
      screen.getByRole("button", {
        name:
          language === "en"
            ? /Start research|View progress/
            : /開始研究|查看進度/,
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("tw-research-start-full-research")).toHaveLength(1);
    expect(screen.getByTestId("tw-research-investor-counts").textContent).toMatch(/73/);
    expect(screen.getByTestId("tw-research-next-action").textContent?.length).toBeGreaterThan(
      10,
    );
    // Job history option shows a date, not the id; no raw snake_case in options.
    const history = screen.getByTestId("tw-research-first-pass-job-history");
    expect(history.textContent).toMatch(/2026/);
    const options = within(history).getAllByRole("option");
    expect(options.length).toBeGreaterThan(0);
    for (const option of options) {
      const label = option.textContent ?? "";
      // No raw snake_case API tokens (underscores) in the visible dropdown.
      expect(label).not.toMatch(/complete_attempted|_/);
    }
    // Raw status · quality stays under Advanced only.
    expect(advanced).toMatch(/running/);
    expect(advanced).toMatch(/incomplete/);
  }

  it("zh-TW", async () => {
    await assertPlainDefaultUi("zh-TW");
  });

  it("en", async () => {
    await assertPlainDefaultUi("en");
  });

  async function assertTabsManageDialogsAndEmpty(language: "zh-TW" | "en") {
    seed({});
    const user = userEvent.setup();
    const { container } = renderPage(language);

    await waitForBucket("paused_manual");
    for (const tab of [
      language === "en" ? /Research progress/i : /研究進度/,
      language === "en" ? /Watchlist/i : /觀察/,
      language === "en" ? /Progress & data quality/i : /進度與資料品質/,
      language === "en" ? /Evidence/i : /證據/,
    ]) {
      const tabEl = screen.queryByRole("tab", { name: tab });
      if (tabEl) await user.click(tabEl);
    }

    expect(await screen.findByTestId("tw-research-job-delete")).toBeInTheDocument();
    expect(screen.getByTestId("tw-research-job-archive")).toBeInTheDocument();
    expect(screen.getByTestId("tw-research-job-cancel")).toBeInTheDocument();

    await user.click(screen.getByTestId("tw-research-job-archive"));
    expect(await screen.findByTestId("tw-research-confirm-modal")).toBeInTheDocument();
    await user.click(screen.getByText(language === "en" ? "Keep working" : "繼續保留"));

    await user.click(screen.getByTestId("tw-research-job-delete"));
    expect(await screen.findByTestId("tw-research-delete-modal")).toBeInTheDocument();
    await user.click(screen.getByText(language === "en" ? "Keep data" : "保留資料"));

    const clone = container.cloneNode(true) as HTMLElement;
    clone
      .querySelectorAll('[data-testid="tw-research-advanced-details"]')
      .forEach((node) => node.remove());
    const visible = clone.textContent ?? "";
    expect(visible).not.toMatch(FORBIDDEN);
    expect(visible).not.toMatch(SNAKE_CASE);

    cleanup();
    seed({ run: null });
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([]);
    renderPage(language);
    expect(await screen.findByTestId("tw-research-first-pass-jobs-empty")).toBeInTheDocument();
  }

  it("tabs + manage dialogs + empty (zh-TW)", async () => {
    await assertTabsManageDialogsAndEmpty("zh-TW");
  });

  it("tabs + manage dialogs + empty (en)", async () => {
    await assertTabsManageDialogsAndEmpty("en");
  });

  async function assertTechnicalScanProgressPlain(language: "zh-TW" | "en") {
    const { scan, progress } = technicalScanFixture();
    seed({
      progress: { leaseActive: false },
      run: { firstPassDrain: { armed: false, available: false } },
    });
    vi.mocked(fetchTwResearchScans).mockResolvedValue({
      scans: [
        {
          scanId: SCAN_ID,
          tradingDay: scan.tradingDay,
          universeVersion: scan.universeVersion,
          status: scan.status,
          qualityStatus: scan.qualityStatus ?? "processed",
          targetCount: scan.targetCount,
          universeSize: scan.universeSize,
          cursor: scan.cursor,
          remainingCount: scan.remainingCount,
          updatedAt: scan.updatedAt ?? "",
          workerActivity: "idle",
          selectorMode: "starter_preset",
          themeKeys: [],
          stockIds: [],
        },
      ],
    });
    vi.mocked(fetchTwResearchScan).mockResolvedValue(scan);
    vi.mocked(fetchTwResearchScanProgress).mockResolvedValue(progress);

    const user = userEvent.setup();
    const { container } = renderPage(language, { initialScanId: SCAN_ID });
    // Single-flow layout: progress is a secondary disclosure, not a tab panel.
    const progressToggle = screen.getByTestId("tw-research-tab-progress");
    const details = progressToggle.closest("details");
    if (details && !details.open) {
      await user.click(progressToggle);
    }
    const tile = await screen.findByTestId("tw-research-progress");
    expect(screen.getByTestId("tw-research-status-line").textContent).toMatch(
      language === "en" ? /Processing finished/ : /處理已結束/,
    );
    expect(screen.getByTestId("tw-research-quality").textContent).toMatch(
      language === "en" ? /Partial/ : /部分完成/,
    );

    const clone = tile.cloneNode(true) as HTMLElement;
    clone
      .querySelectorAll('[data-testid="tw-research-advanced-details"]')
      .forEach((node) => node.remove());
    const visibleText = clone.textContent ?? "";
    expect(visibleText).not.toMatch(SNAKE_CASE);
    expect(visibleText).not.toContain(SCAN_ID);

    const advanced = Array.from(
      tile.querySelectorAll('[data-testid="tw-research-advanced-details"]'),
    )
      .map((node) => node.textContent ?? "")
      .join(" ");
    expect(advanced).toMatch(/completed_processing/);
    expect(advanced).toMatch(/processed/);
    const scanIdInput = within(tile).getByTestId("tw-research-scan-id");
    expect(scanIdInput).toHaveValue(SCAN_ID);
    expect(scanIdInput.closest('[data-testid="tw-research-advanced-details"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="tw-research-scan-id"]')).not.toBeNull();
  }

  it("technical-scan progress tile hides snake_case and scan id (zh-TW)", async () => {
    await assertTechnicalScanProgressPlain("zh-TW");
  });

  it("technical-scan progress tile hides snake_case and scan id (en)", async () => {
    await assertTechnicalScanProgressPlain("en");
  });

  it("complete_attempted never renders Complete/完整 in the default UI", async () => {
    seed({
      job: {
        status: "completed",
        qualityStatus: "complete_attempted",
        activeSnapshotId: "fps_1",
        cursor: 73,
      },
      progress: {
        status: "completed",
        qualityStatus: "complete_attempted",
        cursor: 73,
        processedCount: 73,
        pendingCount: 0,
        okCount: 65,
        partialCount: 5,
        failedCount: 3,
        activeSnapshotId: "fps_1",
      },
      run: {
        phase: "awaiting_candidate_selection",
        status: "first_pass_ready",
        firstPassSnapshotId: "fps_1",
      },
    });
    renderPage("zh-TW");
    await waitForBucket("ready_for_candidates");
    const history = screen.getByTestId("tw-research-first-pass-job-history");
    const options = within(history).getAllByRole("option");
    expect(options.length).toBeGreaterThan(0);
    for (const option of options) {
      const label = option.textContent ?? "";
      // Standalone Complete/完整 only — ignore substrings like 不完整.
      expect(label).not.toMatch(/(?<![不])完整|\bComplete\b/);
      expect(label).toMatch(/已全部嘗試/);
      expect(label).not.toMatch(/complete_attempted|_/);
    }
  });
});

describe("TwResearchPage request error copy (#350)", () => {
  it("HTTP 200 leaseHeld shows no-op wait banner", async () => {
    const user = userEvent.setup();
    seed({ progress: { leaseActive: false } });
    vi.mocked(runTwResearchFirstPassBatch).mockResolvedValue({
      jobId: JOB_ID,
      status: "running",
      leaseHeld: true,
      leaseLost: false,
      budgetStopped: false,
      batchIds: [],
      batchCounts: { ok: 0, partial: 0, failed: 0 },
      processed: 0,
    });
    renderPage();
    await waitForBucket("paused_manual");
    await user.click(await screen.findByTestId("tw-research-run-first-pass-batch"));
    expect(await screen.findByTestId("tw-research-batch-lease-held")).toHaveTextContent(
      "已有一批資料正在收集中，請稍候再試",
    );
    expect(screen.queryByTestId("tw-research-batch-summary")).not.toBeInTheDocument();
  });

  it("HTTP 200 leaseLost shows batch-incomplete banner", async () => {
    const user = userEvent.setup();
    seed({ progress: { leaseActive: false } });
    vi.mocked(runTwResearchFirstPassBatch).mockResolvedValue({
      jobId: JOB_ID,
      status: "running",
      leaseHeld: false,
      leaseLost: true,
      budgetStopped: false,
      batchIds: ["TW:2330"],
      batchCounts: { ok: 0, partial: 0, failed: 0 },
      processed: 0,
    });
    renderPage();
    await waitForBucket("paused_manual");
    await user.click(await screen.findByTestId("tw-research-run-first-pass-batch"));
    expect(await screen.findByTestId("tw-research-batch-incomplete")).toHaveTextContent(
      "本批未完成，將在下一次執行時繼續",
    );
  });

  it("HTTP 200 budgetStopped with processed 0 shows batch-incomplete banner", async () => {
    const user = userEvent.setup();
    seed({ progress: { leaseActive: false } });
    vi.mocked(runTwResearchFirstPassBatch).mockResolvedValue({
      jobId: JOB_ID,
      status: "running",
      leaseHeld: false,
      leaseLost: false,
      budgetStopped: true,
      batchIds: [],
      batchCounts: { ok: 0, partial: 0, failed: 0 },
      processed: 0,
    });
    renderPage();
    await waitForBucket("paused_manual");
    await user.click(await screen.findByTestId("tw-research-run-first-pass-batch"));
    expect(await screen.findByTestId("tw-research-batch-incomplete")).toHaveTextContent(
      "本批未完成，將在下一次執行時繼續",
    );
  });

  it("first-pass batch 409 job-failed uses plain copy; raw detail is advanced-only", async () => {
    const user = userEvent.setup();
    seed({ progress: { leaseActive: false } });
    vi.mocked(runTwResearchFirstPassBatch).mockRejectedValue(
      new ApiError("Request failed: 409", {
        status: 409,
        detail: JSON.stringify({ error: "first_pass_job_failed", lastError: "boom" }),
        kind: "unknown",
        requestId: "req-409",
      }),
    );
    renderPage();
    await waitForBucket("paused_manual");
    await user.click(await screen.findByTestId("tw-research-run-first-pass-batch"));

    const block = await screen.findByTestId("tw-research-error-first-pass-batch");
    expect(block).toHaveTextContent("這次研究的資料收集已停止");
    const visible = block.cloneNode(true) as HTMLElement;
    visible
      .querySelectorAll('[data-testid="tw-research-advanced-details"]')
      .forEach((node) => node.remove());
    expect(visible.textContent).not.toMatch(/first_pass_job_failed|req-409/);
    const raw = within(block).getByTestId("tw-research-error-first-pass-batch-raw");
    expect(raw).toHaveTextContent("first_pass_job_failed");
    expect(raw).toHaveTextContent("request req-409");
  });

  it("first-pass batch 429 in English", async () => {
    const user = userEvent.setup();
    seed({});
    vi.mocked(runTwResearchFirstPassBatch).mockRejectedValue(
      new ApiError("Request failed: 429", { status: 429, detail: "slow down", kind: "unknown" }),
    );
    renderPage("en");
    await waitForBucket("paused_manual");
    await user.click(await screen.findByTestId("tw-research-run-first-pass-batch"));
    expect(
      await screen.findByTestId("tw-research-error-first-pass-batch"),
    ).toHaveTextContent("Too many requests");
  });
});

describe("TwResearchPage polling guard (#350)", () => {
  it("does not poll faster than the existing refetch intervals over 60s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    seed({ progress: { leaseActive: true } });
    renderPage();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    const runBefore = vi.mocked(fetchTwResearchRunCurrent).mock.calls.length;
    const progressBefore = vi.mocked(fetchTwResearchFirstPassProgress).mock.calls.length;
    expect(runBefore).toBeGreaterThanOrEqual(1);
    expect(progressBefore).toBeGreaterThanOrEqual(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    const runCalls = vi.mocked(fetchTwResearchRunCurrent).mock.calls.length;
    const progressCalls = vi.mocked(fetchTwResearchFirstPassProgress).mock.calls.length;
    // Run query: 5s interval; progress query: 4s interval (+1 for the initial fetch).
    expect(runCalls).toBeLessThanOrEqual(Math.floor(60_000 / 5_000) + 1 + 1);
    expect(progressCalls).toBeLessThanOrEqual(Math.floor(60_000 / 4_000) + 1 + 1);
    // And polling is actually bounded by (not far below) those intervals.
    expect(runCalls).toBeGreaterThan(runBefore);
    expect(progressCalls).toBeGreaterThan(progressBefore);
  });
});

describe("TwResearchPage adopt resume (#359)", () => {
  const ADOPT_REASONS = [
    "research_run_retained",
    "concurrent_update_retry",
    "first_pass_job_lease_active",
    "first_pass_job_archived",
    "first_pass_job_nothing_pending",
    "first_pass_job_universe_digest_mismatch",
    "first_pass_job_deletion_in_progress",
    "first_pass_job_not_eligible_status",
    "first_pass_job_already_attached",
  ] as const;

  function seedStandaloneEligible() {
    seed({
      run: null,
      job: {
        status: "partial",
        universeDigest: "digest-73",
        universeSize: 73,
        stockIds: Array.from({ length: 73 }, (_, i) => `TW:${8000 + i}`),
      },
      progress: {
        status: "partial",
        universeSize: 73,
        processedCount: 1,
        pendingCount: 72,
        okCount: 1,
        partialCount: 0,
        failedCount: 0,
        cursor: 99,
      },
    });
  }

  it("modal confirm sends adopt request with universe digest", async () => {
    const user = userEvent.setup();
    seedStandaloneEligible();
    vi.mocked(adoptTwResearchFirstPassJob).mockResolvedValue(
      runRecord({ firstPassJobId: JOB_ID, status: "running" }),
    );
    renderPage("zh-TW");
    const resume = await screen.findByTestId("tw-research-adopt-first-pass");
    await user.click(resume);
    const modal = await screen.findByTestId("tw-research-confirm-modal");
    // Progress API counts (ok+partial+failed), not cursor.
    expect(modal).toHaveTextContent("1/73");
    expect(modal).toHaveTextContent("72");
    expect(modal).not.toHaveTextContent("99/73");
    await user.click(screen.getByTestId("tw-research-confirm-modal-confirm"));
    await waitFor(() => {
      expect(adoptTwResearchFirstPassJob).toHaveBeenCalledWith({
        jobId: JOB_ID,
        confirmUniverseDigest: "digest-73",
      });
    });
  });

  /** Match real HTTP client normalization (detail stringified; body keeps object). */
  function httpConflictApiError(reason: string): ApiError {
    const detailObj = { error: reason, reason };
    const detail = JSON.stringify(detailObj);
    return new ApiError(`Request failed: 409 - ${detail}`, {
      status: 409,
      detail,
      kind: "unknown",
      body: { detail: detailObj },
    });
  }

  it.each(ADOPT_REASONS)(
    "maps 409 %s to a plain sentence without snake_case",
    async (reason) => {
      const user = userEvent.setup();
      seedStandaloneEligible();
      vi.mocked(adoptTwResearchFirstPassJob).mockRejectedValue(
        httpConflictApiError(reason),
      );
      renderPage("en");
      await user.click(await screen.findByTestId("tw-research-adopt-first-pass"));
      await user.click(await screen.findByTestId("tw-research-confirm-modal-confirm"));
      const err = await screen.findByTestId("tw-research-adopt-error");
      expect(err.textContent).toBeTruthy();
      expect(err.textContent).not.toMatch(SNAKE_CASE);
      expect(err.textContent).not.toContain(reason);
      expect(screen.queryByTestId("tw-research-adopt-error-detail")).toBeNull();
    },
  );

  it("unknown 409 reason shows generic copy; raw code only in detail", async () => {
    const user = userEvent.setup();
    seedStandaloneEligible();
    vi.mocked(adoptTwResearchFirstPassJob).mockRejectedValue(
      httpConflictApiError("weird_new_reason"),
    );
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-adopt-first-pass"));
    await user.click(await screen.findByTestId("tw-research-confirm-modal-confirm"));
    const err = await screen.findByTestId("tw-research-adopt-error");
    expect(err.textContent).not.toMatch(SNAKE_CASE);
    expect(err.textContent).not.toContain("weird_new_reason");
    const detail = await screen.findByTestId("tw-research-adopt-error-detail");
    expect(detail).toHaveTextContent("weird_new_reason");
  });

  it("disables confirm when the selected job has no universe digest", async () => {
    const user = userEvent.setup();
    vi.mocked(adoptTwResearchFirstPassJob).mockClear();
    seed({
      run: null,
      job: {
        status: "partial",
        universeSize: 73,
        stockIds: Array.from({ length: 73 }, (_, i) => `TW:${8000 + i}`),
      },
      progress: {
        status: "partial",
        universeSize: 73,
        processedCount: 1,
        pendingCount: 72,
        okCount: 1,
        partialCount: 0,
        failedCount: 0,
      },
    });
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-adopt-first-pass"));
    const confirm = await screen.findByTestId("tw-research-confirm-modal-confirm");
    expect(confirm).toBeDisabled();
    expect(await screen.findByTestId("tw-research-adopt-missing-digest")).toBeInTheDocument();
    expect(adoptTwResearchFirstPassJob).not.toHaveBeenCalled();
  });
});

describe("TwResearchPage Start retry when queued without job (#365)", () => {
  beforeEach(() => {
    vi.mocked(fetchThemeOptions).mockResolvedValue([]);
    vi.mocked(fetchTwResearchScans).mockResolvedValue({ scans: [] });
    vi.mocked(fetchTwResearchScan).mockRejectedValue(
      new ApiError("missing", { status: 404, detail: "x", kind: "not_found" }),
    );
    vi.mocked(startTwResearchRun).mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("keeps Start enabled when run is queued without firstPassJobId", async () => {
    seed({
      run: {
        status: "queued",
        phase: "phase1_first_pass",
        firstPassJobId: null,
        jobClaimExpiresAt: null,
      },
      job: { jobId: "fpj_other", status: "completed", activeSnapshotId: "snap_old" },
      progress: { jobId: "fpj_other", status: "completed", activeSnapshotId: "snap_old" },
    });
    renderPage("en");
    const start = await screen.findByTestId("tw-research-start-full-research");
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
    expect(start).toHaveTextContent(/Start research/i);
    expect(await screen.findByTestId("tw-research-start-retry-hint")).toBeInTheDocument();
  });

  it("shows waiting hint while creation claim is still active", async () => {
    const future = new Date(Date.now() + 60_000).toISOString();
    seed({
      run: {
        status: "queued",
        phase: "phase1_first_pass",
        firstPassJobId: null,
        pendingFirstPassJobId: "fpj_pending",
        jobClaimActive: true,
        jobClaimExpiresAt: future,
      },
    });
    // Keep GET returning the live claim so auto-poll does not 404-fail.
    vi.mocked(fetchTwResearchRunCurrent).mockResolvedValue(
      runRecord({
        status: "queued",
        phase: "phase1_first_pass",
        firstPassJobId: null,
        pendingFirstPassJobId: "fpj_pending",
        jobClaimActive: true,
        jobClaimExpiresAt: future,
      }),
    );
    renderPage("en");
    expect(await screen.findByTestId("tw-research-start-outcome")).toHaveTextContent(
      /already in progress|正在建立/i,
    );
    // Avoid duplicate Start POSTs while the create claim is live.
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-start-full-research")).toBeDisabled();
    });
  });

  it("allows a second Start after incomplete first attempt", async () => {
    const user = userEvent.setup();
    seed({
      run: {
        status: "queued",
        phase: "phase1_first_pass",
        firstPassJobId: null,
      },
    });
    vi.mocked(startTwResearchRun)
      .mockResolvedValueOnce(
        runRecord({
          status: "queued",
          firstPassJobId: null,
          jobClaimExpiresAt: null,
        }),
      )
      .mockResolvedValueOnce(
        runRecord({
          status: "running",
          firstPassJobId: "fpj_recovered",
          execution: {
            state: "executor_unavailable",
            authorized: false,
            drainAvailable: false,
          },
        }),
      );
    renderPage("en");
    const start = await screen.findByTestId("tw-research-start-full-research");
    await user.click(start);
    await waitFor(() => {
      expect(startTwResearchRun).toHaveBeenCalledTimes(1);
    });
    // Still no attached job → Start remains the recovery action.
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
    await user.click(start);
    await waitFor(() => {
      expect(startTwResearchRun).toHaveBeenCalledTimes(2);
    });
  });

  it("shows View progress (enabled) when an authoritative job is attached and armed", async () => {
    seed({
      run: {
        status: "queued",
        phase: "phase1_first_pass",
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "execution_authorized",
          authorized: true,
          drainAvailable: true,
        },
      },
    });
    renderPage("en");
    const start = await screen.findByTestId("tw-research-start-full-research");
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
    expect(start).toHaveTextContent(/View progress|查看進度/i);
    expect(screen.queryByTestId("tw-research-start-retry-hint")).toBeNull();
  });

  it("keeps Start enabled to re-arm when job exists but schedule is off (#366)", async () => {
    const user = userEvent.setup();
    seed({
      run: {
        status: "running",
        phase: "phase1_first_pass",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
          blocker: "first_pass_drain_globally_disabled",
        },
      },
    });
    const armed = runRecord({
      status: "running",
      firstPassJobId: JOB_ID,
      scheduleEnabled: true,
      execution: {
        state: "executor_not_ticking",
        authorized: true,
        drainAvailable: true,
      },
    });
    vi.mocked(startTwResearchRun).mockResolvedValue(armed);
    renderPage("en");
    const start = await screen.findByTestId("tw-research-start-full-research");
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
    const hint = await screen.findByTestId("tw-research-start-retry-hint");
    expect(hint).toHaveTextContent(/automatic collection is not available|自動收集尚未可用/i);
    expect(hint).not.toHaveTextContent(/actively|collecting|正在進行/i);
    // After Start succeeds, refetch returns armed-but-not-ticking.
    vi.mocked(fetchTwResearchRunCurrent).mockResolvedValue(armed);
    await user.click(start);
    await waitFor(() => {
      expect(startTwResearchRun).toHaveBeenCalled();
    });
    expect(
      await screen.findByTestId("tw-research-execution-status"),
    ).toHaveTextContent(/no automatic run has started|尚未開始/i);
    expect(screen.getByTestId("tw-research-execution-status")).not.toHaveTextContent(
      /actively collecting|正在背景收集|正在進行資料收集/i,
    );
  });

  it("shows executor-unavailable copy without inventing active collection (#362)", async () => {
    seed({
      run: {
        status: "running",
        phase: "phase1_first_pass",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        createdAt: "2026-11-16T08:00:00Z",
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
        },
      },
      jobs: [jobRecord({ jobId: JOB_ID, createdAt: "2026-11-17T03:17:00Z" })],
    });
    renderPage("zh-TW");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/研究尚未開始：背景處理服務尚未啟用/);
    expect(status).not.toHaveTextContent(/正在背景收集|正在進行資料收集/);
    const active = await screen.findByTestId("tw-research-active-job");
    // Job label uses Job createdAt (Taipei), not the older Research Run birth time.
    expect(active).toHaveTextContent(/2026\/11\/17/);
    expect(active).not.toHaveTextContent(/2026\/11\/16/);
  });

  it("keeps selected job identity when it is missing from the history list (#362 D2)", async () => {
    seed({
      run: {
        status: "running",
        phase: "phase1_first_pass",
        firstPassJobId: "fpj_selected_missing",
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
        },
      },
      // Older historical jobs only — selected id is absent until refetch.
      jobs: [jobRecord({ jobId: "fpj_older_history", createdAt: "2026-11-16T08:00:00Z" })],
    });
    renderPage("zh-TW");
    const select = await screen.findByTestId("tw-research-first-pass-job-history");
    expect(select).toHaveValue("fpj_selected_missing");
    expect(select).toHaveTextContent(/已選工作（資料載入中/i);
    expect(select).not.toHaveTextContent(/fpj_selected_missing/);
    expect(select).not.toHaveValue("fpj_older_history");
  });

  it("shows investor startOutcome for job_created with Taipei time and no raw ids (#362)", async () => {
    const user = userEvent.setup();
    seed({
      run: null,
      jobs: [],
    });
    vi.mocked(startTwResearchRun).mockResolvedValue({
      ...runRecord({
        researchRunId: "rr_brand_new",
        createdAt: "2026-11-13T02:00:00Z",
        firstPassJobId: "fpj_brand_new",
        execution: {
          state: "job_created",
          authorized: false,
          drainAvailable: true,
          processedCount: 0,
          universeSize: 73,
        },
      }),
      startOutcome: "job_created",
    });
    renderPage("zh-TW");
    const start = await screen.findByTestId("tw-research-start-full-research");
    await user.click(start);
    const outcome = await screen.findByTestId("tw-research-start-outcome");
    expect(outcome).toHaveTextContent(/已建立新的研究與收集工作（建立於 2026\/11\/13/);
    expect(outcome).not.toHaveTextContent(/rr_brand_new|fpj_/);
    expect(outcome).not.toHaveTextContent(/尚未啟用|背景處理服務/);
  });

  it("shows investor startOutcome for job_resumed with progress and no raw ids (#362)", async () => {
    const user = userEvent.setup();
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        createdAt: "2026-11-10T04:00:00Z",
        execution: {
          state: "job_created",
          authorized: false,
          drainAvailable: true,
          processedCount: 12,
          universeSize: 73,
        },
      },
    });
    vi.mocked(startTwResearchRun).mockResolvedValue({
      ...runRecord({
        researchRunId: "rr_resumed",
        createdAt: "2026-11-10T04:00:00Z",
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "executor_not_ticking",
          authorized: true,
          drainAvailable: true,
          processedCount: 12,
          universeSize: 73,
        },
      }),
      startOutcome: "job_resumed",
    });
    renderPage("zh-TW");
    await user.click(await screen.findByTestId("tw-research-start-full-research"));
    const outcome = await screen.findByTestId("tw-research-start-outcome");
    expect(outcome).toHaveTextContent(/已接續現有研究（建立於 2026\/11\/10/);
    expect(outcome).toHaveTextContent(/已處理 12\/73/);
    expect(outcome).not.toHaveTextContent(/rr_resumed|fpj_/);
    expect(outcome).not.toHaveTextContent(/尚未啟用|背景處理服務/);
  });

  it("shows identity-only startOutcome for drain_rearmed (#362)", async () => {
    const user = userEvent.setup();
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
        },
      },
    });
    vi.mocked(startTwResearchRun).mockResolvedValue({
      ...runRecord({
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "executor_not_ticking",
          authorized: true,
          drainAvailable: true,
        },
      }),
      startOutcome: "drain_rearmed",
    });
    renderPage("zh-TW");
    await user.click(await screen.findByTestId("tw-research-start-full-research"));
    expect(await screen.findByTestId("tw-research-start-outcome")).toHaveTextContent(
      /已重新授權現有研究的自動收集/,
    );
  });

  it("job_created execution with drain available uses awaitingStartToArm, not not-enabled (#362)", async () => {
    seed({
      run: {
        status: "running",
        phase: "phase1_first_pass",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        execution: {
          state: "job_created",
          authorized: false,
          drainAvailable: true,
          processedCount: 0,
          universeSize: 73,
        },
      },
      progress: { processedCount: 0, universeSize: 73, pendingCount: 73 },
    });
    renderPage("en");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/Collection is ready\. Press Start to authorize/i);
    expect(status).not.toHaveTextContent(/not enabled|background processing service is not enabled/i);
    expect(status).toHaveTextContent(/0\/73/);
    expect(status).toHaveTextContent(/Last data progress: —/);
    expect(status).not.toHaveTextContent(/Last executor tick|Job updated/i);
    expect(
      await screen.findByTestId("tw-research-execution-advanced-timestamps"),
    ).toHaveTextContent(/Last executor tick: —/);
  });

  it("appends 0/73 and empty tick/progress on executor_unavailable (#362)", async () => {
    seed({
      run: {
        status: "running",
        phase: "phase1_first_pass",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
          processedCount: 0,
          universeSize: 73,
          drainLastTickAt: null,
          jobUpdatedAt: null,
          lastDataProgressAt: null,
        },
        firstPassDrain: { armed: false, available: false, lastTickAt: null },
      },
      progress: {
        processedCount: 0,
        universeSize: 73,
        pendingCount: 73,
        cursor: 0,
        updatedAt: null,
      },
    });
    renderPage("zh-TW");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/研究尚未開始：背景處理服務尚未啟用/);
    expect(status).toHaveTextContent(/0\/73/);
    expect(status).toHaveTextContent(/最後資料進度：—/);
    expect(status).not.toHaveTextContent(/最後執行器心跳|工作狀態更新/);
    expect(status).not.toHaveTextContent(/正在背景收集|正在進行資料收集/);
  });

  it("does not treat jobUpdatedAt as data progress without lastDataProgressAt (#362)", async () => {
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
          processedCount: 1,
          universeSize: 73,
          // Admin/lease bump only — must not become "last data progress".
          jobUpdatedAt: "2026-11-17T04:00:00Z",
          lastDataProgressAt: null,
          drainLastTickAt: "2026-11-17T03:55:00Z",
        },
      },
      progress: { processedCount: 1, universeSize: 73, pendingCount: 72 },
    });
    renderPage("en");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/Last data progress: —/);
    expect(status).not.toHaveTextContent(/Job updated|Last executor tick/i);
    const advanced = await screen.findByTestId(
      "tw-research-execution-advanced-timestamps",
    );
    expect(advanced).toHaveTextContent(/Job updated:.*2026/);
    expect(advanced).toHaveTextContent(/Last executor tick:.*2026/);
  });

  it("shows lastDataProgressAt when symbol evidence was persisted (#362)", async () => {
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
          processedCount: 3,
          universeSize: 73,
          jobUpdatedAt: "2026-11-17T05:00:00Z",
          lastDataProgressAt: "2026-11-17T03:10:00Z",
          drainLastTickAt: null,
        },
      },
      progress: { processedCount: 3, universeSize: 73 },
    });
    renderPage("en");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/Last data progress:.*2026/);
    expect(status).toHaveTextContent(/3\/73/);
  });

  it("waiting_for_retry uses scheduled nextRetryAt and is not executor failure (#362)", async () => {
    seed({
      run: {
        status: "waiting_for_next_window",
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "waiting_for_retry",
          authorized: true,
          drainAvailable: true,
          processedCount: 12,
          universeSize: 73,
          lastDataProgressAt: "2026-11-17T03:10:00Z",
        },
        // Nested progress supplies authoritative retry time after refresh.
        firstPassProgress: {
          nextRetryAt: "2026-11-13T04:30:00Z",
          processedCount: 12,
          universeSize: 73,
        },
      },
      progress: {
        status: "deferred",
        processedCount: 12,
        universeSize: 73,
        nextRetryAt: "2026-11-13T04:30:00Z",
      },
    });
    renderPage("en");
    const status = await screen.findByTestId("tw-research-execution-status");
    await waitFor(() => {
      expect(status).toHaveTextContent(/Scheduled retry:.*2026/);
    });
    expect(status).toHaveTextContent(/waiting to retry|rate limit|temporary error/i);
    expect(status).not.toHaveTextContent(/no automatic run has started/i);
    expect(status).not.toHaveTextContent(/background service/i);
    expect(status).toHaveTextContent(/12\/73/);
  });

  it("waiting_for_retry without nextRetryAt does not invent a time (#362)", async () => {
    seed({
      run: {
        status: "waiting_for_next_window",
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "waiting_for_retry",
          authorized: true,
          drainAvailable: true,
          processedCount: 4,
          universeSize: 73,
        },
        firstPassProgress: { nextRetryAt: null },
      },
      progress: {
        status: "deferred",
        processedCount: 4,
        universeSize: 73,
        nextRetryAt: null,
      },
    });
    renderPage("zh-TW");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/等待重試/);
    expect(status).toHaveTextContent(/不是執行器故障/);
    expect(status).not.toHaveTextContent(/已排定重試：\d{4}/);
    expect(status).not.toHaveTextContent(/自動執行尚未開始/);
  });

  it("execution_authorized stays distinct from waiting_for_retry (#362)", async () => {
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "execution_authorized",
          authorized: true,
          drainAvailable: true,
          processedCount: 0,
          universeSize: 73,
        },
      },
      progress: { processedCount: 0, universeSize: 73, nextRetryAt: null },
    });
    renderPage("en");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/authorized and waiting for the next automatic run/i);
    expect(status).not.toHaveTextContent(/rate limit|temporary error|Scheduled retry/i);
    expect(status).not.toHaveTextContent(/no automatic run has started/i);
  });

  it("executor_not_ticking stays distinct from waiting_for_retry (#362)", async () => {
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "executor_not_ticking",
          authorized: true,
          drainAvailable: true,
          processedCount: 0,
          universeSize: 73,
        },
      },
    });
    renderPage("en");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/no automatic run has started/i);
    expect(status).not.toHaveTextContent(/Scheduled retry|rate limit|temporary error/i);
  });

  it("stalled stays distinct from waiting_for_retry (#362)", async () => {
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: true,
        execution: {
          state: "stalled",
          authorized: true,
          drainAvailable: true,
          processedCount: 10,
          universeSize: 73,
          lastDataProgressAt: "2026-11-17T02:00:00Z",
        },
      },
      progress: { processedCount: 10, universeSize: 73 },
    });
    renderPage("en");
    const stalled = await screen.findByTestId("tw-research-execution-status");
    expect(stalled).toHaveTextContent(/paused mid-way/i);
    expect(stalled).not.toHaveTextContent(/Scheduled retry|rate limit/i);
  });

  it("executor_unavailable stays distinct from waiting_for_retry (#362)", async () => {
    seed({
      run: {
        status: "running",
        firstPassJobId: JOB_ID,
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
          processedCount: 0,
          universeSize: 73,
        },
      },
    });
    renderPage("zh-TW");
    const status = await screen.findByTestId("tw-research-execution-status");
    expect(status).toHaveTextContent(/背景處理服務尚未啟用/);
    expect(status).not.toHaveTextContent(/等待重試|速率限制/);
  });

  it("historical Job progress cannot contaminate current-run execution status (#362)", async () => {
    const user = userEvent.setup();
    const CURRENT = "fpj_current_run";
    const HISTORICAL = "fpj_historical_browse";
    const currentRetry = "2026-11-13T04:30:00Z"; // 12:30 Taipei
    const historicalRetry = "2026-11-14T08:00:00Z"; // 16:00 Taipei

    vi.mocked(fetchTwResearchRunCurrent).mockResolvedValue(
      runRecord({
        status: "waiting_for_next_window",
        firstPassJobId: CURRENT,
        scheduleEnabled: true,
        execution: {
          state: "waiting_for_retry",
          authorized: true,
          drainAvailable: true,
          processedCount: 12,
          universeSize: 73,
          lastDataProgressAt: "2026-11-17T03:10:00Z",
        },
        firstPassProgress: {
          nextRetryAt: currentRetry,
          processedCount: 12,
          universeSize: 73,
        },
      }),
    );
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([
      jobRecord({
        jobId: CURRENT,
        createdAt: "2026-11-17T02:00:00Z",
        status: "deferred",
      }),
      jobRecord({
        jobId: HISTORICAL,
        createdAt: "2026-11-10T02:00:00Z",
        status: "deferred",
      }),
    ]);
    vi.mocked(fetchTwResearchFirstPassJob).mockImplementation(async (jobId: string) =>
      jobRecord({
        jobId,
        status: "deferred",
        createdAt:
          jobId === CURRENT ? "2026-11-17T02:00:00Z" : "2026-11-10T02:00:00Z",
      }),
    );
    vi.mocked(fetchTwResearchFirstPassProgress).mockImplementation(
      async (jobId: string) => {
        if (jobId === HISTORICAL) {
          return progressRecord({
            jobId: HISTORICAL,
            status: "deferred",
            processedCount: 50,
            universeSize: 73,
            pendingCount: 23,
            nextRetryAt: historicalRetry,
          });
        }
        return progressRecord({
          jobId: CURRENT,
          status: "deferred",
          processedCount: 12,
          universeSize: 73,
          pendingCount: 61,
          nextRetryAt: currentRetry,
        });
      },
    );

    renderPage("en");
    const exec = await screen.findByTestId("tw-research-execution-status");
    await waitFor(() => {
      expect(exec).toHaveTextContent(/Scheduled retry:.*12:30/);
    });
    expect(exec).toHaveTextContent(/12\/73/);
    expect(exec).not.toHaveTextContent(/16:00/);
    expect(exec).not.toHaveTextContent(/50\/73/);

    const select = await screen.findByTestId("tw-research-first-pass-job-history");
    await user.selectOptions(select, HISTORICAL);
    expect(select).toHaveValue(HISTORICAL);

    // Investor summary may reflect the browsed historical Job…
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-next-retry")).toHaveTextContent(
        /2026\/11\/14.*16:00|11\/14\/2026.*16:00/,
      );
    });
    // …but the active Run execution line must keep the run-owned retry/counts.
    expect(exec).toHaveTextContent(/Scheduled retry:.*12:30/);
    expect(exec).toHaveTextContent(/12\/73/);
    expect(exec).not.toHaveTextContent(/16:00/);
    expect(exec).not.toHaveTextContent(/50\/73/);

    // Switch back to current Job — still the run-owned schedule.
    await user.selectOptions(select, CURRENT);
    expect(select).toHaveValue(CURRENT);
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-next-retry")).toHaveTextContent(
        /12:30/,
      );
    });
    expect(exec).toHaveTextContent(/Scheduled retry:.*12:30/);
    expect(exec).toHaveTextContent(/12\/73/);
  });

  it("ignores stale historical progress when switching back to current Job (#362)", async () => {
    const user = userEvent.setup();
    const CURRENT = "fpj_current_stale";
    const HISTORICAL = "fpj_hist_stale";
    let resolveHistorical: ((value: Record<string, unknown>) => void) | null = null;

    vi.mocked(fetchTwResearchRunCurrent).mockResolvedValue(
      runRecord({
        status: "waiting_for_next_window",
        firstPassJobId: CURRENT,
        scheduleEnabled: true,
        execution: {
          state: "waiting_for_retry",
          authorized: true,
          drainAvailable: true,
          processedCount: 8,
          universeSize: 73,
          lastDataProgressAt: null,
        },
        firstPassProgress: {
          nextRetryAt: "2026-11-13T05:00:00Z",
          processedCount: 8,
          universeSize: 73,
        },
      }),
    );
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([
      jobRecord({ jobId: CURRENT, status: "deferred" }),
      jobRecord({ jobId: HISTORICAL, status: "deferred" }),
    ]);
    vi.mocked(fetchTwResearchFirstPassJob).mockImplementation(async (jobId: string) =>
      jobRecord({ jobId, status: "deferred" }),
    );
    vi.mocked(fetchTwResearchFirstPassProgress).mockImplementation(
      async (jobId: string) => {
        if (jobId === HISTORICAL) {
          return new Promise((resolve) => {
            resolveHistorical = resolve;
          });
        }
        return progressRecord({
          jobId: CURRENT,
          status: "deferred",
          processedCount: 8,
          universeSize: 73,
          nextRetryAt: "2026-11-13T05:00:00Z",
        });
      },
    );

    renderPage("en");
    const exec = await screen.findByTestId("tw-research-execution-status");
    await waitFor(() => {
      expect(exec).toHaveTextContent(/Scheduled retry:.*13:00/);
    });

    const select = await screen.findByTestId("tw-research-first-pass-job-history");
    await user.selectOptions(select, HISTORICAL);
    // Switch back before historical progress resolves (stale in-flight).
    await user.selectOptions(select, CURRENT);
    resolveHistorical?.(
      progressRecord({
        jobId: HISTORICAL,
        status: "deferred",
        processedCount: 99,
        universeSize: 73,
        nextRetryAt: "2026-11-20T10:00:00Z",
      }),
    );

    await waitFor(() => {
      expect(select).toHaveValue(CURRENT);
    });
    // Stale historical retry/counts must not appear on the Run execution line.
    expect(exec).toHaveTextContent(/Scheduled retry:.*13:00/);
    expect(exec).toHaveTextContent(/8\/73/);
    expect(exec).not.toHaveTextContent(/99\/73/);
    expect(exec).not.toHaveTextContent(/18:00|10:00/);
  });

  it("keeps selected historical job and shows retry on 404 detail load (#362 P2-A)", async () => {
    seed({
      run: {
        status: "running",
        phase: "phase1_first_pass",
        firstPassJobId: "fpj_selected_missing",
        scheduleEnabled: false,
        execution: {
          state: "executor_unavailable",
          authorized: false,
          drainAvailable: false,
        },
      },
      jobs: [jobRecord({ jobId: "fpj_older_history", createdAt: "2026-11-16T08:00:00Z" })],
    });
    vi.mocked(fetchTwResearchFirstPassJob).mockRejectedValue(
      new ApiError("missing", { status: 404, detail: "x", kind: "not_found" }),
    );
    renderPage("en");
    const select = await screen.findByTestId("tw-research-first-pass-job-history");
    expect(select).toHaveValue("fpj_selected_missing");
    expect(
      await screen.findByTestId("tw-research-first-pass-job-detail-error"),
    ).toHaveTextContent(/Could not load that historical collection job \(404\)/i);
    expect(select).toHaveValue("fpj_selected_missing");
    expect(select).not.toHaveValue("fpj_older_history");
    expect(
      screen.getByTestId("tw-research-first-pass-job-detail-retry"),
    ).toBeInTheDocument();
  });

  it("ignores stale historical job detail responses after another selection (#362 P2-A)", async () => {
    const user = userEvent.setup();
    let resolveMissing: ((job: Record<string, unknown>) => void) | null = null;
    vi.mocked(fetchTwResearchRunCurrent).mockResolvedValue(
      runRecord({
        status: "gated",
        phase: "awaiting_analyst_candidate_selection",
        firstPassJobId: "fpj_missing_a",
        scheduleEnabled: false,
      }),
    );
    vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([
      jobRecord({ jobId: "fpj_listed", createdAt: "2026-11-16T08:00:00Z" }),
    ]);
    vi.mocked(fetchTwResearchFirstPassProgress).mockResolvedValue(
      progressRecord({ jobId: "fpj_listed" }),
    );
    vi.mocked(fetchTwResearchFirstPassJob).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveMissing = resolve;
        }),
    );
    renderPage("en");
    const select = await screen.findByTestId("tw-research-first-pass-job-history");
    await waitFor(() => {
      expect(select).toHaveValue("fpj_missing_a");
    });
    await user.selectOptions(select, "fpj_listed");
    expect(select).toHaveValue("fpj_listed");
    resolveMissing?.({
      jobId: "fpj_missing_a",
      activeSnapshotId: "snap_stale",
      status: "completed",
    });
    await waitFor(() => {
      expect(screen.queryByTestId("tw-research-first-pass-job-detail-error")).toBeNull();
    });
    expect(select).toHaveValue("fpj_listed");
  });

  it("shows creation_in_progress startOutcome without claiming resume (#362 P1-A)", async () => {
    const user = userEvent.setup();
    seed({ run: null, jobs: [] });
    const pendingRun = runRecord({
      firstPassJobId: null,
      pendingFirstPassJobId: "fpj_pending",
      jobClaimActive: true,
      jobClaimExpiresAt: new Date(Date.now() + 120_000).toISOString(),
      execution: { state: "queued" },
    });
    let started = false;
    vi.mocked(startTwResearchRun).mockImplementation(async () => {
      started = true;
      return { ...pendingRun, startOutcome: "creation_in_progress" };
    });
    vi.mocked(fetchTwResearchRunCurrent).mockImplementation(async () => {
      if (!started) {
        throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
      }
      return pendingRun;
    });
    renderPage("zh-TW");
    await user.click(await screen.findByTestId("tw-research-start-full-research"));
    expect(await screen.findByTestId("tw-research-start-outcome")).toHaveTextContent(
      /收集工作正在建立中/,
    );
    expect(startTwResearchRun).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-start-full-research")).toBeDisabled();
    });
  });

  it("polls until First-Pass Job attaches after creation_in_progress (#362)", async () => {
    const user = userEvent.setup();
    seed({ run: null, jobs: [] });
    const pendingRun = runRecord({
      firstPassJobId: null,
      pendingFirstPassJobId: "fpj_pending",
      jobClaimActive: true,
      jobClaimExpiresAt: new Date(Date.now() + 120_000).toISOString(),
      createdAt: "2026-11-13T02:00:00Z",
    });
    const attachedRun = runRecord({
      firstPassJobId: "fpj_pending",
      pendingFirstPassJobId: null,
      jobClaimActive: false,
      createdAt: "2026-11-13T02:00:00Z",
      execution: {
        state: "job_created",
        drainAvailable: true,
        processedCount: 0,
        universeSize: 73,
      },
    });
    let started = false;
    let polls = 0;
    vi.mocked(startTwResearchRun).mockImplementation(async () => {
      started = true;
      return { ...pendingRun, startOutcome: "creation_in_progress" };
    });
    vi.mocked(fetchTwResearchRunCurrent).mockImplementation(async () => {
      if (!started) {
        throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
      }
      polls += 1;
      return polls >= 2 ? attachedRun : pendingRun;
    });
    renderPage("zh-TW");
    await user.click(await screen.findByTestId("tw-research-start-full-research"));
    await waitFor(
      () => {
        expect(screen.getByTestId("tw-research-start-outcome")).toHaveTextContent(
          /收集工作已就緒/,
        );
      },
      { timeout: 5_000 },
    );
    expect(startTwResearchRun).toHaveBeenCalledTimes(1);
    expect(polls).toBeGreaterThanOrEqual(2);
  });

  it("stops creation polling on 404 without posting Start again (#362)", async () => {
    const user = userEvent.setup();
    seed({ run: null, jobs: [] });
    let started = false;
    vi.mocked(startTwResearchRun).mockImplementation(async () => {
      started = true;
      return {
        ...runRecord({
          firstPassJobId: null,
          pendingFirstPassJobId: "fpj_pending",
          jobClaimActive: true,
          jobClaimExpiresAt: new Date(Date.now() + 120_000).toISOString(),
        }),
        startOutcome: "creation_in_progress",
      };
    });
    vi.mocked(fetchTwResearchRunCurrent).mockImplementation(async () => {
      if (!started) {
        throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
      }
      throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
    });
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-start-full-research"));
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-start-outcome")).toHaveTextContent(
        /Could not confirm collection setup/i,
      );
    });
    expect(startTwResearchRun).toHaveBeenCalledTimes(1);
  });

  it("times out creation polling with a retry hint (#362)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    seed({ run: null, jobs: [] });
    const pendingRun = runRecord({
      firstPassJobId: null,
      pendingFirstPassJobId: "fpj_pending",
      jobClaimActive: true,
      jobClaimExpiresAt: new Date(Date.now() + 600_000).toISOString(),
    });
    let started = false;
    vi.mocked(startTwResearchRun).mockImplementation(async () => {
      started = true;
      return { ...pendingRun, startOutcome: "creation_in_progress" };
    });
    vi.mocked(fetchTwResearchRunCurrent).mockImplementation(async () => {
      if (!started) {
        throw new ApiError("missing", { status: 404, detail: "x", kind: "not_found" });
      }
      return pendingRun;
    });
    renderPage("en");
    await user.click(await screen.findByTestId("tw-research-start-full-research"));
    expect(await screen.findByTestId("tw-research-start-outcome")).toHaveTextContent(
      /already in progress/i,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50_000);
    });
    await waitFor(() => {
      expect(screen.getByTestId("tw-research-start-outcome")).toHaveTextContent(
        /Still waiting for collection setup/i,
      );
    });
    expect(startTwResearchRun).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("cancels creation polling on unmount (#362)", async () => {
    const user = userEvent.setup();
    seed({ run: null, jobs: [] });
    const pendingRun = runRecord({
      firstPassJobId: null,
      pendingFirstPassJobId: "fpj_pending",
      jobClaimActive: true,
      jobClaimExpiresAt: new Date(Date.now() + 120_000).toISOString(),
    });
    let started = false;
    let resolvePoll: ((run: ReturnType<typeof runRecord>) => void) | null = null;
    vi.mocked(startTwResearchRun).mockImplementation(async () => {
      started = true;
      return { ...pendingRun, startOutcome: "creation_in_progress" };
    });
    vi.mocked(fetchTwResearchRunCurrent).mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          if (!started) {
            reject(
              new ApiError("missing", { status: 404, detail: "x", kind: "not_found" }),
            );
            return;
          }
          resolvePoll = resolve;
        }),
    );
    const view = renderPage("en");
    await user.click(await screen.findByTestId("tw-research-start-full-research"));
    expect(await screen.findByTestId("tw-research-start-outcome")).toHaveTextContent(
      /already in progress/i,
    );
    view.unmount();
    resolvePoll?.(
      runRecord({
        firstPassJobId: "fpj_pending",
        createdAt: "2026-11-13T02:00:00Z",
      }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(startTwResearchRun).toHaveBeenCalledTimes(1);
  });
});
