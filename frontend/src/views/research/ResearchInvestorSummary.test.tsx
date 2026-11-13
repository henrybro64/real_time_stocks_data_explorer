import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import {
  ResearchInvestorSummary,
  type BatchResultView,
} from "./ResearchInvestorSummary";

const NOW = Date.parse("2026-11-13T04:00:00Z");
const freshTick = new Date(NOW - 4 * 60 * 1000).toISOString(); // 11:56 Taipei
const unfinished = {
  status: "running",
  universeSize: 73,
  processedCount: 20,
  pendingCount: 53,
  okCount: 20,
  partialCount: 0,
  failedCount: 0,
  cursor: 20,
};

type Props = Parameters<typeof ResearchInvestorSummary>[0];

async function renderSummary(props: Props, language: "zh-TW" | "en" = "zh-TW") {
  window.localStorage.setItem("stock-ticker-language", language);
  const view = render(
    <LanguageProvider>
      <ResearchInvestorSummary now={NOW} {...props} />
    </LanguageProvider>,
  );
  // LanguageProvider applies the stored language in an effect.
  await waitFor(() => {
    const status = screen.queryByTestId("tw-research-plain-status");
    expect(status).toBeInTheDocument();
  });
  return view;
}

afterEach(() => {
  window.localStorage.clear();
});

const text = (id: string) => screen.getByTestId(id).textContent ?? "";

describe("ResearchInvestorSummary", () => {
  it("shows not-started copy for 0/73 pending-style progress", async () => {
    await renderSummary({
      progress: {
        status: "pending",
        universeSize: 73,
        processedCount: 0,
        pendingCount: 73,
        okCount: 0,
        partialCount: 0,
        failedCount: 0,
        cursor: 0,
      },
      displayedJobId: "fpj_demo",
      drain: { armed: false, available: false },
    });
    expect(screen.getByTestId("tw-research-investor-summary")).toBeInTheDocument();
    expect(text("tw-research-plain-status")).toMatch(/尚未開始/);
    expect(text("tw-research-investor-counts")).toMatch(/73/);
    expect(text("tw-research-next-action")).toMatch(/沒有自動收集/);
  });

  it("promises wait-for-auto only when drain is armed", async () => {
    await renderSummary({
      progress: {
        status: "pending",
        universeSize: 73,
        processedCount: 0,
        pendingCount: 73,
        cursor: 0,
      },
      drain: { armed: true, available: true },
    });
    expect(text("tw-research-next-action")).toMatch(/等待自動收集/);
  });

  it("collecting_now (20/73): in-progress copy and the only safe-to-reload line", async () => {
    await renderSummary({
      progress: { ...unfinished, leaseActive: true },
      drain: { armed: false, available: false },
    });
    expect(screen.getByTestId("tw-research-plain-status")).toHaveAttribute(
      "data-bucket",
      "collecting_now",
    );
    expect(text("tw-research-plain-status")).toBe("正在收集資料（本批進行中）");
    expect(text("tw-research-investor-counts")).toMatch(/已處理 20/);
    expect(text("tw-research-next-action")).toMatch(/重新整理頁面不會中斷進度/);
  });

  it("collecting_now English copy", async () => {
    await renderSummary(
      { progress: { ...unfinished, leaseActive: true } },
      "en",
    );
    expect(text("tw-research-plain-status")).toBe(
      "Collecting data (this batch in progress)",
    );
    expect(text("tw-research-next-action")).toMatch(/Reloading this page is safe/);
  });

  it("paused_manual: running + cursor>0 + no lease + drain off is never 'collecting'", async () => {
    await renderSummary({
      progress: { ...unfinished, leaseActive: false },
      drain: { armed: false, available: false },
    });
    expect(text("tw-research-plain-status")).toBe("目前暫停：沒有正在執行的收集");
    expect(text("tw-research-plain-status")).not.toMatch(/正在收集/);
    expect(text("tw-research-next-action")).not.toMatch(/重新整理/);
    expect(text("tw-research-next-action")).toMatch(/手動批次/);
  });

  it("paused_manual English copy", async () => {
    await renderSummary(
      { progress: unfinished, drain: { armed: false, available: false } },
      "en",
    );
    expect(text("tw-research-plain-status")).toBe("Paused: no collection is running");
    expect(text("tw-research-next-action")).not.toMatch(/reload/i);
  });

  it("paused_auto shows the last run formatted in Taipei time, not raw ISO", async () => {
    await renderSummary({
      progress: unfinished,
      drain: { armed: true, available: true, lastTickAt: freshTick },
    });
    const status = text("tw-research-plain-status");
    expect(status).toMatch(/等待下一次自動收集（上次執行 .*11:56.*）/);
    expect(status).not.toMatch(/T0\d:|Z/);
    expect(text("tw-research-next-action")).not.toMatch(/重新整理/);
  });

  it("paused_auto English copy with Taipei time", async () => {
    await renderSummary(
      {
        progress: unfinished,
        drain: { armed: true, available: true, lastTickAt: freshTick },
      },
      "en",
    );
    expect(text("tw-research-plain-status")).toMatch(
      /^Waiting for the next automatic collection \(last run .*11:56.*\)$/,
    );
  });

  it("paused_auto_stale when armed but no recent tick (or no tick at all)", async () => {
    const old = new Date(NOW - 40 * 60 * 1000).toISOString();
    const first = await renderSummary({
      progress: unfinished,
      drain: { armed: true, available: true, lastTickAt: old },
    });
    expect(text("tw-research-plain-status")).toBe(
      "自動收集已啟用，但最近沒有執行紀錄",
    );
    first.unmount();
    await renderSummary(
      { progress: unfinished, drain: { armed: true, available: true } },
      "en",
    );
    expect(text("tw-research-plain-status")).toBe(
      "Automatic collection is on, but there is no recent run record",
    );
  });

  it("renders waiting_retry with a formatted next retry time", async () => {
    await renderSummary({
      progress: {
        ...unfinished,
        status: "deferred",
        failedCount: 4,
        nextRetryAt: "2026-11-13T04:30:00Z", // 12:30 Taipei
        updatedAt: "2026-11-13T03:50:00Z", // 11:50 Taipei
      },
    });
    expect(text("tw-research-plain-status")).toMatch(/等待下次重試/);
    expect(text("tw-research-next-retry")).toMatch(/12:30/);
    expect(text("tw-research-next-retry")).not.toMatch(/2026-11-13T/);
    expect(text("tw-research-progress-updated")).toMatch(/11:50/);
    expect(text("tw-research-failures-so-far")).toMatch(/目前 4 檔失敗，已排定重試（.*12:30.*）/);
  });

  it("unfinished with only failures so far is not all-failed: shows scheduled retry line", async () => {
    await renderSummary({
      progress: {
        status: "running",
        universeSize: 73,
        processedCount: 10,
        okCount: 0,
        partialCount: 0,
        failedCount: 10,
        pendingCount: 63,
        cursor: 10,
        nextRetryAt: "2026-11-13T04:30:00Z",
      },
      drain: { armed: true, available: true, lastTickAt: freshTick },
    });
    expect(text("tw-research-plain-status")).toMatch(/等待下一次自動收集/);
    expect(text("tw-research-plain-status")).not.toMatch(/沒有可用/);
    expect(text("tw-research-failures-so-far")).toMatch(/目前 10 檔失敗，已排定重試/);
    expect(screen.queryByTestId("tw-research-batch-all-failed")).not.toBeInTheDocument();
  });

  it("terminal job with only failures shows cumulative all-failed status and next action", async () => {
    await renderSummary({
      progress: {
        status: "failed",
        universeSize: 10,
        processedCount: 10,
        okCount: 0,
        partialCount: 0,
        failedCount: 10,
        pendingCount: 0,
      },
    });
    expect(text("tw-research-plain-status")).toMatch(/沒有可用/);
    expect(text("tw-research-next-action")).toMatch(/失敗原因/);
  });

  it("73/73 with gaps is described as attempted, not complete or verified", async () => {
    await renderSummary({
      progress: {
        status: "completed",
        universeSize: 73,
        processedCount: 73,
        okCount: 60,
        partialCount: 5,
        failedCount: 8,
        pendingCount: 0,
        activeSnapshotId: "fps_1",
      },
    });
    expect(text("tw-research-investor-counts")).toMatch(/失敗 8/);
    expect(text("tw-research-investor-counts")).toMatch(/部分完成 5/);
    expect(text("tw-research-plain-status")).toMatch(/已嘗試完成/);
    expect(text("tw-research-plain-status")).not.toMatch(/全部.*(驗證|確認|完成)資料/);
  });

  it("per-batch banner: 10/10 failed with names, codes and plain reasons", async () => {
    const batchResult: BatchResultView = {
      processed: 2,
      ok: 0,
      partial: 0,
      failed: 2,
      failures: [
        { stockId: "TW:2330", label: "台積電 (2330)", reasonClass: "transient" },
        { stockId: "TW:9999", label: "名稱不可用 (9999)", reasonClass: "rate_limited" },
      ],
    };
    await renderSummary({
      // Cumulative job state intentionally different from the batch.
      progress: { ...unfinished, okCount: 20, failedCount: 0, leaseActive: false },
      drain: { armed: true, available: true, lastTickAt: freshTick },
      batchResult,
    });
    expect(text("tw-research-batch-all-failed")).toBe(
      "本批 2 檔資料收集失敗，尚無可驗證資料",
    );
    expect(text("tw-research-batch-failure-TW:2330")).toBe(
      "台積電 (2330) — 暫時無法取得，將自動重試",
    );
    expect(text("tw-research-batch-failure-TW:9999")).toBe(
      "名稱不可用 (9999) — 資料來源限流，稍後重試",
    );
    // Raw reasons never appear by default.
    expect(text("tw-research-batch-failed-symbols")).not.toMatch(
      /timeout|rate_limited|programming_error|transient/i,
    );
    // The cumulative status is unaffected by the batch banner.
    expect(text("tw-research-plain-status")).not.toMatch(/沒有可用/);
  });

  it("per-batch reasons for programming_error and permanent classes", async () => {
    await renderSummary({
      progress: unfinished,
      batchResult: {
        processed: 2,
        ok: 0,
        partial: 0,
        failed: 2,
        failures: [
          { stockId: "TW:1101", label: "台泥 (1101)", reasonClass: "programming_error" },
          { stockId: "TW:1102", label: "亞泥 (1102)", reasonClass: "permanent" },
        ],
      },
    });
    expect(text("tw-research-batch-failure-TW:1101")).toMatch(
      /系統錯誤，已停止並需要修復/,
    );
    expect(text("tw-research-batch-failure-TW:1102")).toMatch(/資料來源沒有此資料/);
  });

  it("does not show the all-failed batch banner when only some of the batch failed", async () => {
    await renderSummary({
      progress: unfinished,
      batchResult: { processed: 10, ok: 7, partial: 0, failed: 3, failures: [] },
    });
    expect(screen.queryByTestId("tw-research-batch-all-failed")).not.toBeInTheDocument();
    expect(text("tw-research-batch-summary")).toMatch(/處理 10 檔 · 已確認 7 · 部分完成 0 · 失敗 3/);
  });

  it("English per-batch banner", async () => {
    await renderSummary(
      {
        progress: unfinished,
        batchResult: {
          processed: 1,
          ok: 0,
          partial: 0,
          failed: 1,
          failures: [
            { stockId: "TW:2330", label: "台積電 (2330)", reasonClass: "permanent" },
          ],
        },
      },
      "en",
    );
    expect(text("tw-research-batch-all-failed")).toBe(
      "1 stocks in this batch failed to collect, and there is no verifiable data yet",
    );
    expect(text("tw-research-batch-failure-TW:2330")).toMatch(
      /The data source has no data for this stock/,
    );
  });

  it("no-op batch: leaseHeld shows wait banner", async () => {
    await renderSummary({
      progress: unfinished,
      batchResult: {
        kind: "lease_held",
        processed: 0,
        ok: 0,
        partial: 0,
        failed: 0,
        failures: [],
      },
    });
    expect(text("tw-research-batch-lease-held")).toBe(
      "已有一批資料正在收集中，請稍候再試",
    );
    expect(screen.queryByTestId("tw-research-batch-summary")).not.toBeInTheDocument();
  });

  it("no-op batch: leaseLost / budgetStopped shows continue-next-run banner", async () => {
    await renderSummary({
      progress: unfinished,
      batchResult: {
        kind: "batch_incomplete",
        processed: 0,
        ok: 0,
        partial: 0,
        failed: 0,
        failures: [],
      },
    });
    expect(text("tw-research-batch-incomplete")).toBe(
      "本批未完成，將在下一次執行時繼續",
    );
  });

  it("no-op batch English leaseHeld copy", async () => {
    await renderSummary(
      {
        progress: unfinished,
        batchResult: {
          kind: "lease_held",
          processed: 0,
          ok: 0,
          partial: 0,
          failed: 0,
          failures: [],
        },
      },
      "en",
    );
    expect(text("tw-research-batch-lease-held")).toBe(
      "Another batch is already collecting; please wait",
    );
  });

  it("stale banner replaces collecting_now with last-known-state copy", async () => {
    await renderSummary({
      progress: { ...unfinished, leaseActive: true },
      drain: { armed: false, available: false },
      stale: true,
      staleAsOf: "2026-11-13T03:58:00Z", // 11:58 Taipei
    });
    expect(text("tw-research-stale-banner")).toMatch(
      /以下為最後已知狀態（.*11:58.*），可能已過時/,
    );
    expect(text("tw-research-plain-status")).not.toMatch(/正在收集資料/);
    expect(screen.getByTestId("tw-research-plain-status")).not.toHaveAttribute(
      "data-bucket",
      "collecting_now",
    );
  });

  it("stale banner English copy", async () => {
    await renderSummary(
      {
        progress: { ...unfinished, leaseActive: true },
        stale: true,
        staleAsOf: "2026-11-13T03:58:00Z",
      },
      "en",
    );
    expect(text("tw-research-stale-banner")).toMatch(
      /^Last known state \(.*11:58.*\); may be out of date$/,
    );
  });

  it("separates browsed job from run-owned job using dates, not ids", async () => {
    await renderSummary({
      progress: {
        status: "completed",
        universeSize: 5,
        processedCount: 5,
        okCount: 5,
        activeSnapshotId: "fps_old",
      },
      displayedJobId: "fpj_old",
      runOwnedJobId: "fpj_active",
      displayedJobCreatedAt: "2026-11-10T02:00:00Z",
      runOwnedJobCreatedAt: "2026-11-13T02:00:00Z",
      browsingHistorical: true,
    });
    const note = text("tw-research-browsing-historical");
    expect(note).toMatch(/2026\/11\/10/);
    expect(note).toMatch(/2026\/11\/13/);
    expect(note).not.toMatch(/fpj_/);
    expect(text("tw-research-active-job")).toMatch(/瀏覽中的已儲存工作（建立於 2026\/11\/10）/);
    expect(text("tw-research-next-action")).toMatch(/瀏覽/);
    expect(screen.getByTestId("tw-research-investor-summary").textContent).not.toMatch(
      /fpj_|fps_|rr_/,
    );
  });

  it("labels the current research by creation date", async () => {
    await renderSummary({
      progress: unfinished,
      displayedJobId: "fpj_active",
      displayedJobCreatedAt: "2026-11-13T02:00:00Z",
    });
    expect(text("tw-research-active-job")).toBe("目前研究（建立於 2026/11/13）");
  });

  it("progress bar exposes an aria-label with the percentage", async () => {
    await renderSummary({ progress: unfinished }); // 20/73 ≈ 27%
    const bar = screen.getByTestId("tw-research-progress-bar");
    expect(bar).toHaveAttribute("aria-valuenow", "27");
    expect(bar).toHaveAttribute("aria-label", "研究進度 27%");
  });

  it("progress bar aria-label in English", async () => {
    await renderSummary({ progress: unfinished }, "en");
    expect(screen.getByTestId("tw-research-progress-bar")).toHaveAttribute(
      "aria-label",
      "Research progress 27%",
    );
  });
});
