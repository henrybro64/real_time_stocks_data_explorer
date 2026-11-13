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
  fetchTwResearchFirstPassProgress,
  fetchTwResearchFirstPassSymbols,
  fetchTwResearchRunCurrent,
  fetchTwResearchScan,
  fetchTwResearchScanProgress,
  fetchTwResearchScans,
  listTwResearchFirstPassJobs,
  researchProgressRevision,
  runTwResearchFirstPassBatch,
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
    fetchTwResearchFirstPassProgress: vi.fn(),
    fetchTwResearchFirstPassSymbols: vi.fn(),
    runTwResearchFirstPassBatch: vi.fn(),
    advanceTwResearchRunGate: vi.fn(async () => ({})),
    startTwResearchRun: vi.fn(),
  };
});

const JOB_ID = "fpj_demo";
const RUN_ID = "rr_demo";
const SCAN_ID = "scan_demo_completed_processing";
const FORBIDDEN = /#\d{3}|Notion|CronJob|GitHub|\blease\b|\bworker\b|fpj_|rr_|offset/i;
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
  progress?: Record<string, unknown>;
  run?: Partial<TwResearchRun> | null;
}) {
  vi.mocked(listTwResearchFirstPassJobs).mockResolvedValue([jobRecord(options.job)]);
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

    // Start action, summary counts and next action are present.
    expect(
      screen.getByRole("button", {
        name: language === "en" ? "Start research" : "開始研究",
      }),
    ).toBeInTheDocument();
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
    await user.click(
      screen.getByRole("tab", {
        name: language === "en" ? /Progress & data quality/i : /進度與資料品質/,
      }),
    );
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
