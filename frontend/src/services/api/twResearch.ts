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

function scanPath(scanId: string, suffix = "") {
  return `/tw-research/scans/${encodeURIComponent(scanId)}${suffix}`;
}

async function postScanMutation(path: string, body: Record<string, unknown>) {
  const response = await requestJsonWithBody<{ scan: TwResearchScanSnapshot }>(path, "POST", body);
  return response.scan;
}

export async function runTwResearchBatch(scanId: string, batchSize = 10) {
  return postScanMutation("/tw-research/scans/batches", { scanId, batchSize });
}

export async function runTwResearchEnrichmentBatch(scanId: string, batchSize = 10) {
  return postScanMutation("/tw-research/scans/enrichment-batches", { scanId, batchSize });
}

export async function fetchTwResearchScan(scanId: string) {
  const response = await requestJson<{ scan: TwResearchScanSnapshot }>(scanPath(scanId));
  return response.scan;
}

export async function fetchTwResearchExportManifest(scanId: string) {
  const response = await requestJson<{ manifest: Record<string, unknown> }>(
    scanPath(scanId, "/export/manifest"),
  );
  return response.manifest;
}

export async function fetchTwResearchExportChunk(
  scanId: string,
  chunkIndex = 0,
  kind: "ledger" | "qualified" | "enriched" = "ledger",
) {
  const response = await requestJson<{ chunk: Record<string, unknown> }>(
    `${scanPath(scanId, "/export/chunk")}?chunkIndex=${chunkIndex}&kind=${kind}`,
  );
  return response.chunk;
}

export async function fetchTwResearchBrowserText(scanId: string, chunkIndex = 0) {
  return requestJson<Record<string, unknown>>(
    `${scanPath(scanId, "/export/browser-text")}?chunkIndex=${chunkIndex}`,
  );
}

/** Latest verified immutable manifest for this user (#319 / #322). */
export async function fetchTwResearchLatestManifest(params?: {
  universeVersion?: string;
  tradingDay?: string;
}) {
  const query = new URLSearchParams();
  if (params?.universeVersion) query.set("universeVersion", params.universeVersion);
  if (params?.tradingDay) query.set("tradingDay", params.tradingDay);
  const suffix = query.size ? `?${query.toString()}` : "";
  const response = await requestJson<{ manifest: Record<string, unknown> }>(
    `/tw-research/exports/latest/manifest${suffix}`,
  );
  return response.manifest;
}

/** Immutable verified snapshot chunk (#319 / #322). */
export async function fetchTwResearchSnapshotChunk(
  snapshotId: string,
  chunkIndex = 0,
  kind: "ledger" | "qualified" | "enriched" = "ledger",
) {
  const response = await requestJson<{ chunk: Record<string, unknown> }>(
    `/tw-research/exports/snapshots/${encodeURIComponent(snapshotId)}/chunk`
      + `?chunkIndex=${chunkIndex}&kind=${kind}`,
  );
  return response.chunk;
}

/** Browser navigation / download URL (includes `/api` prefix via apiUrl). */
export function twResearchCsvUrl(scanId: string) {
  return apiUrl(scanPath(scanId, "/export/csv"));
}
