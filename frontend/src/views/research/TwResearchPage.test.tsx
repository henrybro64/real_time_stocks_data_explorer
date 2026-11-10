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
  fetchTwResearchExportManifest,
  fetchTwResearchLatestManifest,
  fetchTwResearchScan,
  fetchTwResearchScans,
  fetchTwResearchSnapshotChunk,
  researchScanRefetchInterval,
  runTwResearchBatch,
  type TwResearchScanSnapshot,
  type TwResearchScanSummary,
} from "../../services/api/twResearch";
import { TwResearchPage } from "./TwResearchPage";

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

describe("TwResearchPage persisted scans (#327)", () => {
  beforeEach(() => {
    vi.mocked(fetchThemeOptions).mockResolvedValue([]);
    vi.mocked(createTwResearchScan).mockReset();
    vi.mocked(createTwResearchScan).mockResolvedValue(queuedScan());
    vi.mocked(fetchTwResearchExportManifest).mockReset();
    vi.mocked(fetchTwResearchExportManifest).mockResolvedValue({});
    vi.mocked(fetchTwResearchScan).mockReset();
    vi.mocked(fetchTwResearchScan).mockResolvedValue(queuedScan());
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
    vi.mocked(fetchTwResearchScan)
      .mockResolvedValueOnce(queuedScan())
      .mockRejectedValueOnce(http500());
    const { client } = renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByText("TW:2330")).toBeInTheDocument();
    await client.refetchQueries({ queryKey: ["tw-research-scan", SCAN_ID] });
    expect(screen.getByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    expect(screen.getByText("TW:2330")).toBeInTheDocument();
    expect(await screen.findByTestId("tw-research-stale")).toBeInTheDocument();
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });

  it("polls only while work can still progress and does not run a batch", async () => {
    expect(researchScanRefetchInterval("running")).toBe(10_000);
    expect(researchScanRefetchInterval("queued")).toBe(10_000);
    expect(researchScanRefetchInterval("partial")).toBe(10_000);
    expect(researchScanRefetchInterval("budget_exhausted")).toBe(false);
    expect(researchScanRefetchInterval("completed_processing")).toBe(false);
    expect(researchScanRefetchInterval("verified_ready")).toBe(false);
    expect(researchScanRefetchInterval(undefined)).toBe(false);

    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({ status: "running", workerActivity: "unknown", qualityStatus: "source_incomplete" }),
    );
    const { client } = renderPage({ initialScanId: SCAN_ID });
    expect(await screen.findByTestId("tw-research-worker")).toHaveTextContent("沒有有效租約");
    expect(screen.getByTestId("tw-research-quality")).toHaveTextContent("source_incomplete");
    expect(screen.getByTestId("tw-research-quality")).toHaveTextContent("不是已驗證匯出");
    const query = client.getQueryCache().find({ queryKey: ["tw-research-scan", SCAN_ID] });
    const interval = query?.options.refetchInterval;
    expect(typeof interval).toBe("function");
    expect(interval && typeof interval === "function" ? interval(query!) : false).toBe(10_000);
    expect(runTwResearchBatch).not.toHaveBeenCalled();
  });

  it("does not create a duplicate while an equivalent active scan is saved", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchTwResearchScans).mockResolvedValue({ scans: [summary()] });
    renderPage();
    expect(await screen.findByLabelText("掃描 ID")).toHaveValue(SCAN_ID);
    await user.click(screen.getByTestId("tw-research-create"));
    expect(createTwResearchScan).not.toHaveBeenCalled();
    expect(screen.getByTestId("tw-research-existing-scan")).toBeInTheDocument();
  });

  it("shows a budget-exhausted scan as unfinished and not resumable", async () => {
    vi.mocked(fetchTwResearchScan).mockResolvedValue(
      queuedScan({
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
