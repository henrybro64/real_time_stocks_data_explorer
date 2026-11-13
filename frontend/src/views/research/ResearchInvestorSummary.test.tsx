import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ResearchInvestorSummary } from "./ResearchInvestorSummary";

describe("ResearchInvestorSummary", () => {
  it("shows not-started copy for 0/73 pending-style progress", () => {
    render(
      <LanguageProvider>
        <ResearchInvestorSummary
          progress={{
            status: "pending",
            universeSize: 73,
            processedCount: 0,
            pendingCount: 73,
            okCount: 0,
            partialCount: 0,
            failedCount: 0,
            cursor: 0,
          }}
          displayedJobId="fpj_demo"
          drain={{ armed: false, available: false }}
        />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("tw-research-investor-summary")).toBeInTheDocument();
    expect(screen.getByTestId("tw-research-plain-status").textContent).toMatch(
      /尚未開始|Not started/i,
    );
    expect(screen.getByTestId("tw-research-investor-counts").textContent).toMatch(/73/);
    expect(screen.getByTestId("tw-research-next-action").textContent).toMatch(
      /沒有自動收集|not active|manual batch/i,
    );
  });

  it("promises wait-for-auto only when drain is armed", () => {
    render(
      <LanguageProvider>
        <ResearchInvestorSummary
          progress={{
            status: "pending",
            universeSize: 73,
            processedCount: 0,
            pendingCount: 73,
            cursor: 0,
          }}
          drain={{ armed: true, available: true }}
        />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("tw-research-next-action").textContent).toMatch(
      /等待自動收集|wait for automatic/i,
    );
  });

  it("surfaces all-failed status and next-action", () => {
    render(
      <LanguageProvider>
        <ResearchInvestorSummary
          progress={{
            status: "failed",
            universeSize: 10,
            processedCount: 10,
            okCount: 0,
            partialCount: 0,
            failedCount: 10,
            pendingCount: 0,
          }}
          showBatchAllFailed
        />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("tw-research-plain-status").textContent).toMatch(
      /沒有可用|no usable/i,
    );
    expect(screen.getByTestId("tw-research-batch-all-failed").textContent).toMatch(
      /失敗|failed|no usable/i,
    );
    expect(screen.getByTestId("tw-research-next-action").textContent).toMatch(
      /失敗原因|failure reasons|Do not treat/i,
    );
  });

  it("separates browsed job from run-owned job", () => {
    render(
      <LanguageProvider>
        <ResearchInvestorSummary
          progress={{
            status: "completed",
            universeSize: 5,
            processedCount: 5,
            okCount: 5,
            activeSnapshotId: "fps_old",
          }}
          displayedJobId="fpj_old"
          runOwnedJobId="fpj_active"
          browsingHistorical
        />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("tw-research-browsing-historical").textContent).toMatch(
      /fpj_old/,
    );
    expect(screen.getByTestId("tw-research-next-action").textContent).toMatch(
      /瀏覽|browsing/i,
    );
  });
});
