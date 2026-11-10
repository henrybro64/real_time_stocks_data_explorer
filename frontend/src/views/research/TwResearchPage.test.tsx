import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

import { LanguageProvider } from "../../app/i18n";
import { ApiError } from "../../services/api/client";
import {
  createTwResearchScan,
  fetchThemeOptions,
  fetchTwResearchBrowserText,
  fetchTwResearchExportManifest,
  fetchTwResearchLatestManifest,
  fetchTwResearchScan,
  fetchTwResearchScanProgress,
  fetchTwResearchScans,
  fetchTwResearchSnapshotChunk,
  researchProgressRevision,
  researchScanListRefetchInterval,
  researchScanRefetchInterval,
  runTwResearchBatch,
  type TwResearchScanProgress,
  type TwResearchScanSnapshot,
  type TwResearchScanSummary,
} from "../../services/api/twResearch";
import { TwResearchPage, taipeiTradingDay } from "./TwResearchPage";

vi.mock("../../services/api/twResearch", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/twResearch")>(
    "../../services/api/twResearch",
  );
  return {
    ...actual,
    fetchThemeOptions: vi.fn(),
    fetchTwResearchLatestManifest: vi.fn(),
    fetchTwResearchSnapshotChunk: vi.fn(),
    createTwResearchScan: vi.fn(),
    resolveTwResearchUniverse: vi.fn(),
    runTwResearchBatch: vi.fn(),
    runTwResearchEnrichmentBatch: vi.fn(),
    fetchTwResearchScan: vi.fn(),
    fetchTwResearchScanProgress: vi.fn(),
    fetchTwResearchScans: vi.fn(async () => ({ scans: [] })),
    fetchTwResearchExportManifest: vi.fn(),
    fetchTwResearchBrowserText: vi.fn(),
    fetchTwResearchExportChunk: vi.fn(),
  };
});

function renderPage(props: { initialScanId?: string | null } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <TwResearchPage initialScanId={props.initialScanId ?? null} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

describe("TwResearchPage latest verified handoff (#322)", () => {
  beforeEach(() => {
    vi.mocked(fetchThemeOptions).mockResolvedValue([]);
    vi.mocked(fetchTwResearchLatestManifest).mockResolvedValue({
      snapshotId: "example-snapshot",
      contentDigest: "a".repeat(64),
      qualityStatus: "verified_ready",
      tradingDay: "2024-06-28",
      chunkDigests: { "0": "b".repeat(64) },
    });
    vi.mocked(fetchTwResearchSnapshotChunk).mockResolvedValue({
      chunkIndex: 0,
      kind: "ledger",
      checksumSha256: "b".repeat(64),
      rows: [{ stockId: "TW:2330" }],
    });
  });

  it("loads latest verified manifest JSON without scraping stock pages", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByTestId("tw-research-latest-verified")).toBeInTheDocument();
    await user.click(screen.getByTestId("tw-research-load-latest-manifest"));

    await waitFor(() => {
      expect(fetchTwResearchLatestManifest).toHaveBeenCalled();
    });
    await waitFor(() => {
      const area = screen.getByTestId("tw-research-latest-manifest-json") as HTMLTextAreaElement;
      expect(area.value).toContain("example-snapshot");
      expect(area.value).toContain("contentDigest");
    });

    await user.click(screen.getByTestId("tw-research-load-latest-chunk"));
    await waitFor(() => {
      expect(fetchTwResearchSnapshotChunk).toHaveBeenCalledWith(
        "example-snapshot",
        0,
        "ledger",
      );
    });
    await waitFor(() => {
      const area = screen.getByTestId("tw-research-latest-manifest-json") as HTMLTextAreaElement;
      expect(area.value).toContain("TW:2330");
    });
  });
});

const SCAN_ID = "4c6c39f4-8be7-4c29-bc09-65cc4e1f548f";

function queuedScan(overrides: Partial<TwResearchScanSnapshot> = {}): TwResearchScanSnapshot {
  return {
    scanId: SCAN_ID,
    tradingDay: "2026-11-10",
    universeVersion: "tw-starter",
    status: "queued",
    cursor: 0,
    universeSize: 73,
    processedCount: 0,
    remainingCount: 73,
    targetCount: 10,
    dayPassCount: 0,
    qualifiedCount: 0,
    notes: [],
    ledger: [{ stock_id: "TW:2330", stage: "queued" }],
    checkpointsEphemeral: false,
    ...overrides,
  };
}

function http500(requestId?: string) {
  return new ApiError("Request failed: 500", {
    status: 500,
    detail: null,
    kind: "unknown",
    requestId,
  });
}

describe("TwResearchPage operation failures (#328)", () => {
  beforeEach(() => {
    vi.mocked(fetchThemeOptions).mockResolvedValue([]);
    vi.mocked(createTwResearchScan).mockReset();
    vi.mocked(createTwResearchScan).mockResolvedValue(queuedScan());
    vi.mocked(fetchTwResearchExportManifest).mockReset();
    vi.mocked(fetchTwResearchExportManifest).mockResolvedValue({ snapshotId: "manifest" });
    vi.mocked(fetchTwResearchScan).mockReset();
    vi.mocked(fetchTwResearchScan).mockResolvedValue(queuedScan());
    vi.mocked(fetchTwResearchScanProgress).mockReset();
    vi.mocked(fetchTwResearchScanProgress).mockResolvedValue(progress());
    vi.mocked(fetchTwResearchScans).mockReset();
    vi.mocked(fetchTwResearchScans).mockResolvedValue({ scans: [] });
    vi.mocked(runTwResearchBatch).mockReset();
  });

  it("keeps the scan when the export manifest fails and names that operation", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchTwResearchExportManifest).mockRejectedValue(http500("abc12345"));
    renderPage();

    await user.click(screen.getByTestId("tw-research-create"));
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    const manifestError = await screen.findByTestId("tw-research-error-manifest");
    expect(manifestError).toHaveTextContent("匯出 manifest");
    expect(manifestError).toHaveTextContent("Request failed: 500");
    expect(manifestError).toHaveTextContent("request abc12345");
    expect(screen.queryByTestId("tw-research-error-create")).not.toBeInTheDocument();
    expect(screen.getByText("TW:2330")).toBeInTheDocument();

    vi.mocked(fetchTwResearchExportManifest).mockResolvedValue({ snapshotId: "manifest" });
    await user.click(screen.getByTestId("tw-research-refresh"));
    await waitFor(() => {
      expect(screen.queryByTestId("tw-research-error-manifest")).not.toBeInTheDocument();
    });
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
  });

  it("does not create a second scan for the same selection", async () => {
    const user = userEvent.setup();
    let releaseCreate: (scan: TwResearchScanSnapshot) => void = () => undefined;
    vi.mocked(createTwResearchScan).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseCreate = resolve;
        }),
    );
    renderPage();

    await user.click(screen.getByTestId("tw-research-create"));
    await user.click(screen.getByTestId("tw-research-create"));
    expect(createTwResearchScan).toHaveBeenCalledTimes(1);

    releaseCreate(queuedScan());
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    await user.click(screen.getByTestId("tw-research-create"));
    expect(createTwResearchScan).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("tw-research-existing-scan")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("輸出目標（不放寬通過條件）"), "20");
    await user.click(screen.getByTestId("tw-research-create"));
    await waitFor(() => {
      expect(createTwResearchScan).toHaveBeenCalledTimes(2);
    });
  });

  it("clears a create error after a successful retry", async () => {
    const user = userEvent.setup();
    vi.mocked(createTwResearchScan)
      .mockRejectedValueOnce(http500())
      .mockResolvedValueOnce(queuedScan());
    renderPage();

    await user.click(screen.getByTestId("tw-research-create"));
    expect(await screen.findByTestId("tw-research-error-create")).toHaveTextContent("建立掃描");
    await user.click(screen.getByTestId("tw-research-create"));
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(screen.queryByTestId("tw-research-error-create")).not.toBeInTheDocument();
  });

  it("reloads durable progress when a long batch response is lost", async () => {
    const user = userEvent.setup();
    vi.mocked(runTwResearchBatch).mockRejectedValue(http500());
    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({
        status: "running",
        cursor: 10,
        processedCount: 10,
        remainingCount: 63,
      }),
    );
    renderPage();
    await user.click(screen.getByTestId("tw-research-create"));
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);

    await user.click(screen.getByTestId("tw-research-batch"));
    expect(await screen.findByText(/已處理 10\/73/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId("tw-research-error-batch")).not.toBeInTheDocument();
    });
    expect(fetchTwResearchScan).toHaveBeenCalledWith(SCAN_ID);
    expect(createTwResearchScan).toHaveBeenCalledTimes(1);
  });

  it("keeps the previous scan when the batch and the follow-up read both fail", async () => {
    const user = userEvent.setup();
    vi.mocked(runTwResearchBatch).mockRejectedValue(http500());
    vi.mocked(fetchTwResearchScan).mockRejectedValue(
      new ApiError("Request failed: 503", {
        status: 503,
        detail: "database unavailable",
        kind: "unavailable",
      }),
    );
    renderPage();
    await user.click(screen.getByTestId("tw-research-create"));
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);

    await user.click(screen.getByTestId("tw-research-batch"));
    const batchError = await screen.findByTestId("tw-research-error-batch");
    expect(batchError).toHaveTextContent("掃描批次");
    expect(batchError).toHaveTextContent("Request failed: 500");
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(screen.getByText(/已處理 0\/73/)).toBeInTheDocument();
    expect(screen.queryByTestId("tw-research-error-refresh")).not.toBeInTheDocument();
  });

  it("keeps the batch error when the durable snapshot is marked failed", async () => {
    const user = userEvent.setup();
    vi.mocked(runTwResearchBatch).mockRejectedValue(
      new ApiError("Request failed: 503", {
        status: 503,
        detail: "durable_store_unavailable",
        kind: "unavailable",
      }),
    );
    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({
        status: "failed",
        cursor: 4,
        processedCount: 4,
        remainingCount: 69,
        notes: ["persist_symbol:OperationalError"],
      }),
    );
    vi.mocked(fetchTwResearchScanProgress).mockResolvedValue(
      progress({
        status: "failed",
        cursor: 4,
        processedCount: 4,
        remainingCount: 69,
      }),
    );
    renderPage();
    await user.click(screen.getByTestId("tw-research-create"));
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);

    await user.click(screen.getByTestId("tw-research-batch"));
    const batchError = await screen.findByTestId("tw-research-error-batch");
    expect(batchError).toHaveTextContent("掃描批次");
    expect(batchError).toHaveTextContent("503");
    expect(await screen.findByText(/已處理 4\/73/)).toBeInTheDocument();
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
  });

  it("keeps the scan on screen while a batch request is still running", async () => {
    const user = userEvent.setup();
    let releaseBatch: (scan: TwResearchScanSnapshot) => void = () => undefined;
    vi.mocked(runTwResearchBatch).mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseBatch = resolve;
        }),
    );
    renderPage();
    await user.click(screen.getByTestId("tw-research-create"));
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);

    await user.click(screen.getByTestId("tw-research-batch"));
    expect(await screen.findByText("處理中…")).toBeInTheDocument();
    expect(screen.getByTestId("tw-research-create")).toBeDisabled();
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);

    releaseBatch(queuedScan({ status: "running", processedCount: 5, remainingCount: 68, cursor: 5 }));
    expect(await screen.findByText(/已處理 5\/73/)).toBeInTheDocument();
    expect(screen.queryByText("處理中…")).not.toBeInTheDocument();
  });
});

function summary(overrides: Partial<TwResearchScanSummary> = {}): TwResearchScanSummary {
  return {
    scanId: SCAN_ID,
    tradingDay: "2026-11-10",
    universeVersion: "tw-starter",
    status: "queued",
    qualityStatus: "processed",
    targetCount: 10,
    universeSize: 73,
    cursor: 0,
    remainingCount: 73,
    updatedAt: "2026-11-10T08:28:52+08:00",
    workerActivity: "unknown",
    selectorMode: "starter_preset",
    themeKeys: [],
    stockIds: [],
    ...overrides,
  };
}

function progress(overrides: Partial<TwResearchScanProgress> = {}): TwResearchScanProgress {
  const base = {
    scanId: SCAN_ID,
    tradingDay: "2026-11-10",
    universeVersion: "tw-starter",
    status: "queued",
    qualityStatus: "processed",
    cursor: 0,
    enrichCursor: 0,
    universeSize: 73,
    processedCount: 0,
    remainingCount: 73,
    targetCount: 10,
    dayPassCount: 0,
    qualifiedCount: 0,
    enrichmentsRun: 0,
    maxEnrichmentsPerRun: 20,
    upstreamMonthFetches: 0,
    maxUpstreamMonthFetches: 40,
    updatedAt: "2026-11-10T08:28:52+08:00",
    activeSnapshotId: null,
    workerActivity: "unknown" as const,
    includesLedger: false,
    ...overrides,
  };
  return {
    ...base,
    ledgerRevision:
      overrides.ledgerRevision ??
      researchProgressRevision({
        updatedAt: base.updatedAt,
        status: base.status,
        qualityStatus: base.qualityStatus,
        cursor: base.cursor,
        enrichCursor: base.enrichCursor,
        processedCount: base.processedCount,
        upstreamMonthFetches: base.upstreamMonthFetches,
        enrichmentsRun: base.enrichmentsRun,
        workerActivity: base.workerActivity,
        activeSnapshotId: base.activeSnapshotId,
      }),
  };
}

describe("TwResearchPage persisted scans (#327)", () => {
  beforeEach(() => {
    vi.mocked(fetchThemeOptions).mockResolvedValue([]);
    vi.mocked(createTwResearchScan).mockReset();
    vi.mocked(createTwResearchScan).mockResolvedValue(queuedScan());
    vi.mocked(fetchTwResearchExportManifest).mockReset();
    vi.mocked(fetchTwResearchExportManifest).mockResolvedValue({});
    vi.mocked(fetchTwResearchScan).mockReset();
    vi.mocked(fetchTwResearchScan).mockResolvedValue(queuedScan());
    vi.mocked(fetchTwResearchScanProgress).mockReset();
    vi.mocked(fetchTwResearchScanProgress).mockResolvedValue(progress());
    vi.mocked(fetchTwResearchScans).mockReset();
    vi.mocked(fetchTwResearchScans).mockResolvedValue({ scans: [] });
    vi.mocked(runTwResearchBatch).mockReset();
  });

  it("restores the newest saved scan without creating one", async () => {
    vi.mocked(fetchTwResearchScans).mockResolvedValue({ scans: [summary()] });
    renderPage();
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(screen.getByText("TW:2330")).toBeInTheDocument();
    expect(createTwResearchScan).not.toHaveBeenCalled();
    expect(runTwResearchBatch).not.toHaveBeenCalled();
    expect(screen.getByTestId("tw-research-worker")).toHaveTextContent("沒有有效租約");
  });

  it("opens a direct scan link and can switch to another saved scan", async () => {
    const user = userEvent.setup();
    const older = "ddbbf9c7-9fb2-447e-a393-780b6da27611";
    vi.mocked(fetchTwResearchScans).mockResolvedValue({
      scans: [summary(), summary({ scanId: older, status: "completed_processing", cursor: 73, remainingCount: 0 })],
    });
    vi.mocked(fetchTwResearchScan).mockImplementation(async (id: string) =>
      queuedScan({
        scanId: id,
        status: id === older ? "completed_processing" : "queued",
        processedCount: id === older ? 73 : 0,
        remainingCount: id === older ? 0 : 73,
      }),
    );
    renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(createTwResearchScan).not.toHaveBeenCalled();

    await user.selectOptions(screen.getByLabelText("最近的掃描"), older);
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(older);
    expect(fetchTwResearchScan).toHaveBeenCalledWith(older);
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });

  it("keeps the last ledger when a progress read fails", async () => {
    vi.mocked(fetchTwResearchScan).mockResolvedValue(queuedScan());
    vi.mocked(fetchTwResearchScanProgress)
      .mockResolvedValueOnce(progress())
      .mockRejectedValueOnce(http500());
    const { client } = renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByText("TW:2330")).toBeInTheDocument();
    await client.refetchQueries({ queryKey: ["tw-research-progress", SCAN_ID] });
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(screen.getByText("TW:2330")).toBeInTheDocument();
    expect(await screen.findByTestId("tw-research-stale")).toBeInTheDocument();
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });

  it("polls progress while active and does not poll the full ledger on a timer", async () => {
    expect(researchScanRefetchInterval("running")).toBe(10_000);
    expect(researchScanRefetchInterval("queued")).toBe(10_000);
    expect(researchScanRefetchInterval("partial")).toBe(10_000);
    expect(researchScanRefetchInterval("budget_exhausted")).toBe(false);
    expect(researchScanRefetchInterval("completed_processing")).toBe(false);
    expect(researchScanRefetchInterval("verified_ready")).toBe(false);
    expect(researchScanRefetchInterval(undefined)).toBe(false);
    expect(researchScanListRefetchInterval()).toBe(30_000);

    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({ status: "running", workerActivity: "unknown", qualityStatus: "source_incomplete" }),
    );
    vi.mocked(fetchTwResearchScanProgress).mockResolvedValue(
      progress({ status: "running", qualityStatus: "source_incomplete", workerActivity: "unknown" }),
    );
    const { client } = renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByTestId("tw-research-worker")).toHaveTextContent("沒有有效租約");
    expect(screen.getByTestId("tw-research-quality")).toHaveTextContent("source_incomplete");
    expect(screen.getByTestId("tw-research-quality")).toHaveTextContent("不是已驗證匯出");
    const progressQuery = client.getQueryCache().find({ queryKey: ["tw-research-progress", SCAN_ID] });
    const progressInterval = progressQuery?.options.refetchInterval;
    expect(typeof progressInterval).toBe("function");
    expect(
      progressInterval && typeof progressInterval === "function"
        ? progressInterval(progressQuery!)
        : false,
    ).toBe(10_000);
    const scanQuery = client.getQueryCache().find({ queryKey: ["tw-research-scan", SCAN_ID] });
    expect(scanQuery?.options.refetchInterval).toBe(false);
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });

  it("discovers a newly created scan without changing the selected scan", async () => {
    const other = "agent-created-scan-bbbbbbbbbbbb";
    vi.mocked(fetchTwResearchScans)
      .mockResolvedValueOnce({ scans: [summary()] })
      .mockResolvedValueOnce({
        scans: [
          summary({
            scanId: other,
            tradingDay: "2026-11-10",
            status: "queued",
            updatedAt: "2026-11-10T09:00:00+08:00",
          }),
          summary(),
        ],
      });
    vi.mocked(fetchTwResearchScan).mockImplementation(async (id: string) =>
      queuedScan({ scanId: id }),
    );
    vi.mocked(fetchTwResearchScanProgress).mockImplementation(async (id: string) =>
      progress({ scanId: id }),
    );
    const { client } = renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    await client.refetchQueries({ queryKey: ["tw-research-scans"] });
    await waitFor(() => {
      const history = screen.getByTestId("tw-research-history") as HTMLSelectElement;
      expect([...history.options].map((option) => option.value)).toEqual(
        expect.arrayContaining([SCAN_ID, other]),
      );
    });
    expect(screen.getByTestId("tw-research-history")).toHaveValue(SCAN_ID);
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(createTwResearchScan).not.toHaveBeenCalled();
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });

  it("reloads the ledger only when progress revision changes", async () => {
    const unchanged = progress({
      status: "running",
      processedCount: 0,
      cursor: 0,
      updatedAt: "2026-11-10T08:28:52+08:00",
    });
    const changed = progress({
      status: "running",
      processedCount: 10,
      cursor: 10,
      remainingCount: 63,
      updatedAt: "2026-11-10T08:30:00+08:00",
    });
    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({ status: "running", processedCount: 0, cursor: 0 }),
    );
    vi.mocked(fetchTwResearchScanProgress)
      .mockResolvedValueOnce(unchanged)
      .mockResolvedValueOnce(unchanged)
      .mockResolvedValueOnce(changed);
    const { client } = renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    const afterInitial = vi.mocked(fetchTwResearchScan).mock.calls.length;
    await client.refetchQueries({ queryKey: ["tw-research-progress", SCAN_ID] });
    expect(vi.mocked(fetchTwResearchScan).mock.calls.length).toBe(afterInitial);
    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({
        status: "running",
        processedCount: 10,
        cursor: 10,
        remainingCount: 63,
        updatedAt: "2026-11-10T08:30:00+08:00",
        ledger: [{ stock_id: "TW:2330", stage: "A" }, { stock_id: "TW:2454", stage: "A" }],
      }),
    );
    await client.refetchQueries({ queryKey: ["tw-research-progress", SCAN_ID] });
    await waitFor(() => {
      expect(vi.mocked(fetchTwResearchScan).mock.calls.length).toBeGreaterThan(afterInitial);
    });
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });

  it("does not create a duplicate while an equivalent active scan is saved", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchTwResearchScans).mockResolvedValue({
      scans: [summary({ tradingDay: taipeiTradingDay() })],
    });
    renderPage();
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    await user.click(screen.getByTestId("tw-research-create"));
    expect(createTwResearchScan).not.toHaveBeenCalled();
    expect(screen.getByTestId("tw-research-existing-scan")).toBeInTheDocument();
  });

  it("creates a new scan when the matching active scan is for a prior trading day", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchTwResearchScans).mockResolvedValue({
      scans: [summary({ tradingDay: "2020-01-02", status: "partial", cursor: 12 })],
    });
    vi.mocked(createTwResearchScan).mockResolvedValue(
      queuedScan({ scanId: "new-scan-for-today-aaaaaaaa", tradingDay: taipeiTradingDay() }),
    );
    renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    await user.click(screen.getByTestId("tw-research-create"));
    await waitFor(() => {
      expect(createTwResearchScan).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue("new-scan-for-today-aaaaaaaa");
    expect(screen.queryByTestId("tw-research-existing-scan")).not.toBeInTheDocument();
  });

  it("refreshes the export manifest when polled progress revision changes", async () => {
    const unchanged = progress({
      status: "running",
      processedCount: 0,
      cursor: 0,
      qualityStatus: "processed",
      updatedAt: "2026-11-10T08:28:52+08:00",
    });
    const changed = progress({
      status: "running",
      processedCount: 10,
      cursor: 10,
      remainingCount: 63,
      qualityStatus: "verified_ready",
      activeSnapshotId: "snap-1",
      updatedAt: "2026-11-10T08:30:00+08:00",
    });
    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({ status: "running", processedCount: 0, cursor: 0, qualityStatus: "processed" }),
    );
    vi.mocked(fetchTwResearchScanProgress)
      .mockResolvedValueOnce(unchanged)
      .mockResolvedValueOnce(changed);
    vi.mocked(fetchTwResearchExportManifest)
      .mockResolvedValueOnce({ qualityStatus: "processed" })
      .mockResolvedValueOnce({ qualityStatus: "verified_ready", snapshotId: "snap-1" });
    const { client } = renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    await waitFor(() => {
      expect(fetchTwResearchExportManifest).toHaveBeenCalled();
    });
    const afterInitial = vi.mocked(fetchTwResearchExportManifest).mock.calls.length;
    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({
        status: "running",
        processedCount: 10,
        cursor: 10,
        remainingCount: 63,
        qualityStatus: "verified_ready",
        activeSnapshotId: "snap-1",
        updatedAt: "2026-11-10T08:30:00+08:00",
      }),
    );
    await client.refetchQueries({ queryKey: ["tw-research-progress", SCAN_ID] });
    await waitFor(() => {
      expect(vi.mocked(fetchTwResearchExportManifest).mock.calls.length).toBeGreaterThan(
        afterInitial,
      );
    });
  });

  it("clears Agent handoff state when switching saved scans", async () => {
    const user = userEvent.setup();
    const older = "ddbbf9c7-9fb2-447e-a393-780b6da27611";
    vi.mocked(fetchTwResearchScans).mockResolvedValue({
      scans: [summary(), summary({ scanId: older, status: "completed_processing", cursor: 73 })],
    });
    vi.mocked(fetchTwResearchScan).mockImplementation(async (id: string) =>
      queuedScan({
        scanId: id,
        status: id === older ? "completed_processing" : "queued",
        processedCount: id === older ? 73 : 0,
        remainingCount: id === older ? 0 : 73,
      }),
    );
    vi.mocked(fetchTwResearchScanProgress).mockImplementation(async (id: string) =>
      progress({
        scanId: id,
        status: id === older ? "completed_processing" : "queued",
        processedCount: id === older ? 73 : 0,
        remainingCount: id === older ? 0 : 73,
      }),
    );
    vi.mocked(fetchTwResearchBrowserText).mockResolvedValue({
      scanId: SCAN_ID,
      continuation: { totalChunks: 3, nextChunkIndex: 1 },
      rows: [{ stockId: "TW:2330" }],
    });
    renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    await user.click(screen.getByRole("button", { name: "載入 Agent JSON" }));
    await waitFor(() => {
      const area = screen.getByLabelText("瀏覽器可讀匯出 JSON") as HTMLTextAreaElement;
      expect(area.value).toContain("TW:2330");
    });
    expect(screen.getByText(/Agent 交接 chunk 0／3/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("最近的掃描"), older);
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(older);
    await waitFor(() => {
      const area = screen.getByLabelText("瀏覽器可讀匯出 JSON") as HTMLTextAreaElement;
      expect(area.value).toBe("");
    });
    expect(screen.getByText(/Agent 交接 chunk 0／—/)).toBeInTheDocument();
  });

  it("surfaces a recent-scan list failure without hiding cached history", async () => {
    vi.mocked(fetchTwResearchScans)
      .mockResolvedValueOnce({ scans: [summary({ tradingDay: taipeiTradingDay() })] })
      .mockRejectedValueOnce(
        new ApiError("Request failed: 503", {
          status: 503,
          detail: "durable_store_unavailable",
          kind: "unavailable",
        }),
      );
    const { client } = renderPage();
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(screen.getByTestId("tw-research-history")).toBeInTheDocument();
    await client.refetchQueries({ queryKey: ["tw-research-scans"] });
    const listError = await screen.findByTestId("tw-research-error-list");
    expect(listError).toHaveTextContent("最近的掃描");
    expect(listError).toHaveTextContent("503");
    expect(screen.getByTestId("tw-research-history")).toHaveValue(SCAN_ID);
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
  });

  it("shows a budget-exhausted scan as unfinished and not resumable", async () => {
    const exhausted = queuedScan({
      status: "budget_exhausted",
      qualityStatus: "source_incomplete",
      workerActivity: "idle",
      upstreamMonthFetches: 40,
      maxUpstreamMonthFetches: 40,
      ledger: [
        {
          stock_id: "TW:2330",
          stage: "A",
          readiness: "rejected",
          rejection_reason: "history_budget_exhausted",
        },
      ],
    });
    vi.mocked(fetchTwResearchScan).mockResolvedValue(exhausted);
    vi.mocked(fetchTwResearchScanProgress).mockResolvedValue(
      progress({
        status: "budget_exhausted",
        qualityStatus: "source_incomplete",
        workerActivity: "idle",
        upstreamMonthFetches: 40,
        maxUpstreamMonthFetches: 40,
      }),
    );
    renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByTestId("tw-research-budget-exhausted")).toBeInTheDocument();
    expect(screen.getByTestId("tw-research-batch")).toBeDisabled();
    expect(screen.getByTestId("tw-research-quality")).toHaveTextContent("source_incomplete");
    expect(screen.getByTestId("tw-research-unresolved")).toHaveTextContent("history_budget_exhausted");
    expect(screen.getByTestId("tw-research-worker")).toHaveTextContent("沒有 worker 正在處理");
    expect(createTwResearchScan).not.toHaveBeenCalled();
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });
});
