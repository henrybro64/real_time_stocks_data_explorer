import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import {
  fetchThemeOptions,
  fetchTwResearchLatestManifest,
  fetchTwResearchSnapshotChunk,
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
    fetchTwResearchExportManifest: vi.fn(),
    fetchTwResearchBrowserText: vi.fn(),
    fetchTwResearchExportChunk: vi.fn(),
  };
});

function renderPage() {
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
