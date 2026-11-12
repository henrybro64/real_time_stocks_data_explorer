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
  calculationVersion?: string;
  /** ``daily_first`` (legacy) or ``monthly_first`` (candidate #336). */
  stageOrder?: "daily_first" | "monthly_first" | string;
  dailyContinuationPolicy?: string | null;
  status: string;
  cursor: number;
  enrichCursor?: number;
  universeSize: number;
  processedCount: number;
  definitiveEvaluatedCount?: number;
  budgetExhaustedCount?: number;
  sourceIncompleteCount?: number;
  unresolvedCount?: number;
  unprocessedCount?: number;
  remainingCount: number;
  targetCount: number;
  dayPassCount: number;
  monthPassCount?: number;
  firstStagePassCount?: number;
  dualPassCount?: number;
  dailyDeferredCount?: number;
  monthlyIncompleteCount?: number;
  monthlyIncompleteIds?: string[];
  dailyNotRunByDesignCount?: number;
  dailyNotRunByDesignIds?: string[];
  monthlyFailWatchlistCount?: number;
  monthlyFailWatchlistIds?: string[];
  stageProgressSemanticsVersion?: string;
  qualifiedCount?: number;
  qualifiedIds?: string[];
  enrichmentsRun?: number;
  maxEnrichmentsPerRun?: number;
  notes: string[];
  ledger: Array<Record<string, unknown>>;
  checkpointsEphemeral: boolean;
  qualityStatus?: string;
  upstreamMonthFetches?: number;
  maxUpstreamMonthFetches?: number;
  createdAt?: string;
  updatedAt?: string;
  workerActivity?: "active" | "unknown" | "idle";
  activeSnapshotId?: string | null;
  stockIds?: string[];
  /** Present when the scan was created from an analyst candidate set (#336). */
  candidateProvenance?: {
    candidateSetId?: string;
    candidateSetVersion?: number;
    firstPassSnapshotId?: string;
    firstPassDigest?: string;
    approvedStockIds?: string[];
    dailyOverrideIds?: string[];
    universeVersion?: string;
    calculationVersion?: string;
    tradingDay?: string;
    notNotionReportList?: boolean;
  } | null;
}

export type TwResearchScanSummary = {
  scanId: string;
  tradingDay: string;
  universeVersion: string;
  status: string;
  qualityStatus: string;
  targetCount: number;
  universeSize: number;
  cursor: number;
  remainingCount: number;
  updatedAt: string;
  activeSnapshotId?: string | null;
  workerActivity: "active" | "unknown" | "idle";
  selectorMode: TwResearchUniverseMode;
  themeKeys: string[];
  stockIds: string[];
};

export type TwResearchScanProgress = {
  scanId: string;
  tradingDay: string;
  universeVersion: string;
  status: string;
  qualityStatus: string;
  cursor: number;
  enrichCursor?: number;
  universeSize: number;
  processedCount: number;
  definitiveEvaluatedCount?: number;
  budgetExhaustedCount?: number;
  sourceIncompleteCount?: number;
  unresolvedCount?: number;
  unprocessedCount?: number;
  remainingCount: number;
  targetCount: number;
  dayPassCount: number;
  monthPassCount?: number;
  firstStagePassCount?: number;
  dualPassCount?: number;
  dailyDeferredCount?: number;
  monthlyIncompleteCount?: number;
  calculationVersion?: string;
  stageOrder?: "daily_first" | "monthly_first" | string;
  stageProgressSemanticsVersion?: string;
  qualifiedCount?: number;
  enrichmentsRun?: number;
  maxEnrichmentsPerRun?: number;
  upstreamMonthFetches?: number;
  maxUpstreamMonthFetches?: number;
  createdAt?: string;
  updatedAt?: string;
  activeSnapshotId?: string | null;
  workerActivity?: "active" | "unknown" | "idle";
  ledgerRevision: string;
  includesLedger: boolean;
};

/** Read-only poll while a scan can still make progress. Stop on a terminal status. */
export function researchScanRefetchInterval(status: string | undefined): number | false {
  if (status === "queued" || status === "running" || status === "partial") return 10_000;
  return false;
}

/** Recent-scan discovery for Agent/other-session creates. Slower than progress. */
export function researchScanListRefetchInterval(): number {
  return 30_000;
}

/** Keep in sync with backend persistence.progress_revision. */
export function researchProgressRevision(input: {
  updatedAt?: string | null;
  status?: string | null;
  qualityStatus?: string | null;
  cursor?: number | null;
  enrichCursor?: number | null;
  processedCount?: number | null;
  upstreamMonthFetches?: number | null;
  enrichmentsRun?: number | null;
  workerActivity?: string | null;
  activeSnapshotId?: string | null;
}): string {
  return [
    input.updatedAt ?? "",
    input.status ?? "",
    input.qualityStatus ?? "",
    String(input.cursor ?? 0),
    String(input.enrichCursor ?? 0),
    String(input.processedCount ?? 0),
    String(input.upstreamMonthFetches ?? 0),
    String(input.enrichmentsRun ?? 0),
    input.workerActivity ?? "",
    input.activeSnapshotId ?? "",
  ].join("|");
}

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

/** Open another bounded history-fetch window for unresolved symbols (#329). */
export async function resumeTwResearchBudgetWindow(
  scanId: string,
  additionalMonthFetches = 40,
) {
  return postScanMutation("/tw-research/scans/resume-budget", {
    scanId,
    additionalMonthFetches,
  });
}

export async function runTwResearchEnrichmentBatch(scanId: string, batchSize = 10) {
  return postScanMutation("/tw-research/scans/enrichment-batches", { scanId, batchSize });
}

export async function fetchTwResearchScans(limit = 20) {
  const response = await requestJson<{ scans: TwResearchScanSummary[] }>(
    `/tw-research/scans?limit=${limit}`,
  );
  return response;
}

export async function fetchTwResearchScan(scanId: string) {
  const response = await requestJson<{ scan: TwResearchScanSnapshot }>(scanPath(scanId));
  return response.scan;
}

export async function fetchTwResearchScanProgress(scanId: string) {
  const response = await requestJson<{ progress: TwResearchScanProgress }>(
    scanPath(scanId, "/progress"),
  );
  return response.progress;
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

/** Research membership / first-pass job helpers (#335/#336). */
export async function fetchTwResearchMembership() {
  const response = await requestJson<{ membership: Record<string, unknown> }>(
    "/tw-research/membership",
  );
  return response.membership;
}

export async function saveTwResearchMembership(body: Record<string, unknown>) {
  const response = await requestJsonWithBody<{ membership: Record<string, unknown> }>(
    "/tw-research/membership",
    "PUT",
    body,
  );
  return response.membership;
}

export async function previewTwResearchMembership(body: Record<string, unknown>) {
  const response = await requestJsonWithBody<{ membership: Record<string, unknown> }>(
    "/tw-research/membership/preview",
    "POST",
    body,
  );
  return response.membership;
}

export async function listTwResearchFirstPassJobs(limit = 20) {
  const response = await requestJson<{ jobs: Record<string, unknown>[] }>(
    `/tw-research/first-pass/jobs?limit=${encodeURIComponent(String(limit))}`,
  );
  return response.jobs;
}

export async function createTwResearchFirstPassJob(body?: {
  stockIds?: string[];
  useMembership?: boolean;
}) {
  const response = await requestJsonWithBody<{ job: Record<string, unknown> }>(
    "/tw-research/first-pass/jobs",
    "POST",
    body ?? { useMembership: true },
  );
  return response.job;
}

export async function fetchTwResearchFirstPassJob(jobId: string) {
  const response = await requestJson<{ job: Record<string, unknown> }>(
    `/tw-research/first-pass/jobs/${encodeURIComponent(jobId)}`,
  );
  return response.job;
}

export async function runTwResearchFirstPassBatch(
  jobId: string,
  batchSize = 10,
) {
  const response = await requestJsonWithBody<{ job: Record<string, unknown> }>(
    `/tw-research/first-pass/jobs/${encodeURIComponent(jobId)}/batches`,
    "POST",
    { batchSize },
  );
  return response.job;
}

export async function fetchTwResearchFirstPassProgress(jobId: string) {
  const response = await requestJson<{ progress: Record<string, unknown> }>(
    `/tw-research/first-pass/jobs/${encodeURIComponent(jobId)}/progress`,
  );
  return response.progress;
}

export async function fetchTwResearchFirstPassSymbols(
  jobId: string,
  params?: {
    status?: string;
    q?: string;
    completeness?: string;
    offset?: number;
    limit?: number;
  },
) {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.q) query.set("q", params.q);
  if (params?.completeness) query.set("completeness", params.completeness);
  if (params?.offset != null) query.set("offset", String(params.offset));
  if (params?.limit != null) query.set("limit", String(params.limit));
  const suffix = query.size ? `?${query.toString()}` : "";
  const response = await requestJson<{ result: Record<string, unknown> }>(
    `/tw-research/first-pass/jobs/${encodeURIComponent(jobId)}/symbols${suffix}`,
  );
  return response.result;
}

export async function fetchTwResearchFirstPassSnapshot(snapshotId: string) {
  return requestJson<Record<string, unknown>>(
    `/tw-research/first-pass/snapshots/${encodeURIComponent(snapshotId)}`,
  );
}

export async function fetchTwResearchFirstPassManifest(snapshotId: string) {
  return requestJson<Record<string, unknown>>(
    `/tw-research/first-pass/snapshots/${encodeURIComponent(snapshotId)}/manifest`,
  );
}

export async function fetchTwResearchFirstPassChunk(
  snapshotId: string,
  chunkIndex = 0,
) {
  return requestJson<Record<string, unknown>>(
    `/tw-research/first-pass/snapshots/${encodeURIComponent(snapshotId)}/chunk`
      + `?chunkIndex=${chunkIndex}`,
  );
}

export async function submitTwResearchCandidateSet(body: {
  firstPassSnapshotId: string;
  firstPassDigest: string;
  stockIds: string[];
  origin?: "manual" | "analyst";
  expectedVersion?: number;
  selectionReasons?: Record<string, unknown>;
}) {
  const response = await requestJsonWithBody<{ candidateSet: Record<string, unknown> }>(
    "/tw-research/candidate-sets",
    "POST",
    body,
  );
  return response.candidateSet;
}

export async function fetchTwResearchLatestCandidateSet() {
  const response = await requestJson<{ candidateSet: Record<string, unknown> }>(
    "/tw-research/candidate-sets/latest",
  );
  return response.candidateSet;
}

export async function fetchTwResearchCandidateSet(
  candidateSetId: string,
  version: number,
) {
  const response = await requestJson<{ candidateSet: Record<string, unknown> }>(
    `/tw-research/candidate-sets/${encodeURIComponent(candidateSetId)}`
      + `?version=${encodeURIComponent(String(version))}`,
  );
  return response.candidateSet;
}

export async function createTwResearchTechnicalScanFromCandidateSet(body?: {
  expectedVersion?: number;
  candidateSetId?: string;
  targetCount?: number;
  tradingDay?: string;
  dailyOverrideIds?: string[];
}) {
  const response = await requestJsonWithBody<{ scan: TwResearchScanSnapshot }>(
    "/tw-research/candidate-sets/technical-scan",
    "POST",
    body ?? {},
  );
  return response.scan;
}

export type TwResearchRun = {
  researchRunId: string;
  userId: string;
  phase: string;
  status: string;
  tradingDay?: string | null;
  firstPassJobId?: string | null;
  firstPassSnapshotId?: string | null;
  candidateSetId?: string | null;
  candidateSetVersion?: number | null;
  technicalScanId?: string | null;
  gates?: Record<string, unknown>;
  dimensions?: Record<string, unknown>;
  notes?: string[];
  scheduleEnabled?: boolean;
  notNotionPublisher?: boolean;
  cronjobRemainsSuspended?: boolean;
  durable?: boolean;
  createdAt?: string;
  updatedAt?: string;
};

export async function fetchTwResearchRunCurrent() {
  const response = await requestJson<{ researchRun: TwResearchRun }>(
    "/tw-research/research-runs/current",
  );
  return response.researchRun;
}

export async function startTwResearchRun(body?: {
  phase?: "phase1_first_pass";
  tradingDay?: string;
  notes?: string[];
}) {
  const response = await requestJsonWithBody<{ researchRun: TwResearchRun }>(
    "/tw-research/research-runs/start",
    "POST",
    body ?? { phase: "phase1_first_pass" },
  );
  return response.researchRun;
}

export async function advanceTwResearchRunGate(params?: {
  firstPassJobId?: string;
  firstPassSnapshotId?: string;
  jobStatus?: string;
  scanStatus?: string;
}) {
  const query = new URLSearchParams();
  if (params?.firstPassJobId) query.set("firstPassJobId", params.firstPassJobId);
  if (params?.firstPassSnapshotId) {
    query.set("firstPassSnapshotId", params.firstPassSnapshotId);
  }
  if (params?.jobStatus) query.set("jobStatus", params.jobStatus);
  if (params?.scanStatus) query.set("scanStatus", params.scanStatus);
  const suffix = query.size ? `?${query.toString()}` : "";
  const response = await requestJsonWithBody<{ researchRun: TwResearchRun }>(
    `/tw-research/research-runs/gate${suffix}`,
    "POST",
    {},
  );
  return response.researchRun;
}

export async function postTwResearchReportEvidence(body?: {
  tradingDay?: string;
  firstPassSnapshotId?: string;
  scanId?: string;
}) {
  const response = await requestJsonWithBody<{ evidence: Record<string, unknown> }>(
    "/tw-research/report-evidence",
    "POST",
    body ?? {},
  );
  return response.evidence;
}

export async function fetchTwResearchReportEvidenceExampleZhTw() {
  return requestJson<Record<string, unknown>>(
    "/tw-research/report-evidence/example-zh-tw",
  );
}
