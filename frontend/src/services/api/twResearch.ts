import { apiUrl, requestJson, requestJsonWithBody } from "./client";

export type TwResearchUniverseMode = "starter_preset" | "theme_keys" | "stock_ids";

export type ThemeOption = {
  key: string;
  label: string;
};

export type TwResearchScanSnapshot = {
  scanId: string;
  tradingDay: string;
  universeVersion: string;
  status: string;
  cursor: number;
  enrichCursor?: number;
  universeSize: number;
  processedCount: number;
  remainingCount: number;
  targetCount: number;
  dayPassCount: number;
  qualifiedCount?: number;
  qualifiedIds?: string[];
  enrichmentsRun?: number;
  maxEnrichmentsPerRun?: number;
  notes: string[];
  ledger: Array<Record<string, unknown>>;
  checkpointsEphemeral: boolean;
};

export async function fetchTwResearchContract() {
  return requestJson<Record<string, unknown>>("/tw-research/contract");
}

export async function fetchThemeOptions() {
  return requestJson<ThemeOption[]>("/themes");
}

export async function resolveTwResearchUniverse(body: {
  mode: TwResearchUniverseMode;
  themeKeys?: string[];
  stockIds?: string[];
}) {
  return requestJsonWithBody<{
    universeVersion: string;
    mode: string;
    stockIds: string[];
    uniqueCount: number;
    rejected: Array<{ stockId: string; reason: string }>;
    themeKeysApplied: string[];
    notes: string[];
  }>("/tw-research/universe/resolve", "POST", {
    mode: body.mode,
    themeKeys: body.themeKeys ?? [],
    stockIds: body.stockIds ?? [],
  });
}

export async function createTwResearchScan(body: {
  mode: TwResearchUniverseMode;
  themeKeys?: string[];
  stockIds?: string[];
  targetCount?: 10 | 20;
  tradingDay?: string;
}) {
  const response = await requestJsonWithBody<{ scan: TwResearchScanSnapshot }>(
    "/tw-research/scans",
    "POST",
    {
      universe: {
        mode: body.mode,
        themeKeys: body.themeKeys ?? [],
        stockIds: body.stockIds ?? [],
      },
      targetCount: body.targetCount ?? 10,
      tradingDay: body.tradingDay,
    },
  );
  return response.scan;
}

export async function runTwResearchBatch(scanId: string, batchSize = 10) {
  const response = await requestJsonWithBody<{ scan: TwResearchScanSnapshot }>(
    "/tw-research/scans/batches",
    "POST",
    { scanId, batchSize },
  );
  return response.scan;
}

export async function runTwResearchEnrichmentBatch(scanId: string, batchSize = 10) {
  const response = await requestJsonWithBody<{ scan: TwResearchScanSnapshot }>(
    "/tw-research/scans/enrichment-batches",
    "POST",
    { scanId, batchSize },
  );
  return response.scan;
}

export async function fetchTwResearchScan(scanId: string) {
  const response = await requestJson<{ scan: TwResearchScanSnapshot }>(
    `/tw-research/scans/${encodeURIComponent(scanId)}`,
  );
  return response.scan;
}

export async function fetchTwResearchExportManifest(scanId: string) {
  const response = await requestJson<{ manifest: Record<string, unknown> }>(
    `/tw-research/scans/${encodeURIComponent(scanId)}/export/manifest`,
  );
  return response.manifest;
}

export async function fetchTwResearchExportChunk(
  scanId: string,
  chunkIndex = 0,
  kind: "ledger" | "qualified" | "enriched" = "ledger",
) {
  const response = await requestJson<{ chunk: Record<string, unknown> }>(
    `/tw-research/scans/${encodeURIComponent(scanId)}/export/chunk?chunkIndex=${chunkIndex}&kind=${kind}`,
  );
  return response.chunk;
}

export async function fetchTwResearchBrowserText(scanId: string, chunkIndex = 0) {
  const response = await requestJson<Record<string, unknown>>(
    `/tw-research/scans/${encodeURIComponent(scanId)}/export/browser-text?chunkIndex=${chunkIndex}`,
  );
  return response;
}

/** Browser navigation / download URL (includes `/api` prefix via apiUrl). */
export function twResearchCsvUrl(scanId: string) {
  return apiUrl(`/tw-research/scans/${encodeURIComponent(scanId)}/export/csv`);
}
