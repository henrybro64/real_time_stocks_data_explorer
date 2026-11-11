"use client";

import {
  Button,
  Column,
  Grid,
  InlineLoading,
  Pagination,
  Select,
  SelectItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TextArea,
  TextInput,
  Tile,
} from "@carbon/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { useLanguage, type TranslationKey } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { toApiError } from "../../services/api/client";
import {
  createTwResearchFirstPassJob,
  createTwResearchScan,
  fetchThemeOptions,
  fetchTwResearchBrowserText,
  fetchTwResearchExportChunk,
  fetchTwResearchExportManifest,
  fetchTwResearchFirstPassChunk,
  fetchTwResearchFirstPassManifest,
  fetchTwResearchFirstPassProgress,
  fetchTwResearchFirstPassSymbols,
  fetchTwResearchLatestManifest,
  fetchTwResearchMembership,
  fetchTwResearchScan,
  fetchTwResearchScanProgress,
  fetchTwResearchScans,
  fetchTwResearchSnapshotChunk,
  previewTwResearchMembership,
  researchProgressRevision,
  researchScanListRefetchInterval,
  researchScanRefetchInterval,
  resolveTwResearchUniverse,
  resumeTwResearchBudgetWindow,
  runTwResearchBatch,
  runTwResearchEnrichmentBatch,
  runTwResearchFirstPassBatch,
  saveTwResearchMembership,
  submitTwResearchCandidateSet,
  twResearchCsvUrl,
  type TwResearchScanSnapshot,
  type TwResearchUniverseMode,
} from "../../services/api/twResearch";

const PAGE_SIZE = 25;

function splitLines(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((value) => value.trim())
    .filter(Boolean);
}

function researchSelectionKey(input: {
  mode: TwResearchUniverseMode;
  targetCount: number;
  stockIds: string[];
  themeKeys: string[];
}): string {
  return JSON.stringify({
    mode: input.mode,
    targetCount: input.targetCount,
    stockIds: input.mode === "stock_ids" ? input.stockIds : [],
    themeKeys: input.mode === "theme_keys" ? input.themeKeys : [],
  });
}

/** Calendar day the backend uses when create omits tradingDay (Asia/Taipei). */
export function taipeiTradingDay(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

function scanProgressKey(scan: TwResearchScanSnapshot | null): string {
  if (!scan) return "";
  return [
    scan.status,
    scan.processedCount,
    scan.cursor,
    scan.enrichCursor ?? 0,
    scan.qualifiedCount ?? 0,
  ].join(":");
}

/**
 * After a lost/proxy-failed mutation response, a GET may show that the server
 * finished the work. Keep that snapshot, but never treat a durable `failed`
 * status as recovery — that is the backend's explicit failure signal.
 */
function recoveredAfterLostMutation(
  beforeKey: string,
  latest: TwResearchScanSnapshot | null,
): boolean {
  if (!latest || latest.status === "failed") return false;
  return scanProgressKey(latest) !== beforeKey;
}

function describeResearchFailure(error: unknown): string {
  const api = toApiError(error);
  const text = api.detail || api.message;
  return api.requestId ? `${text} · request ${api.requestId}` : text;
}

const ACTIVE_SCAN_STATUSES = new Set(["queued", "running", "partial"]);

function workerActivity(scan: TwResearchScanSnapshot): "active" | "unknown" | "idle" {
  if (
    scan.workerActivity === "active" ||
    scan.workerActivity === "unknown" ||
    scan.workerActivity === "idle"
  ) {
    return scan.workerActivity;
  }
  return ACTIVE_SCAN_STATUSES.has(scan.status) ? "unknown" : "idle";
}

/** Coerce ledger/UI values without Object's "[object Object]" stringification. */
function displayText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function workerActivityLabelKey(
  activity: "active" | "unknown" | "idle",
): TranslationKey {
  if (activity === "active") return "research.tw.workerActive";
  if (activity === "idle") return "research.tw.workerIdle";
  return "research.tw.workerUnknown";
}

export function TwResearchPage({
  initialScanId = null,
}: Readonly<{ initialScanId?: string | null }>) {
  const { t } = useLanguage();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<TwResearchUniverseMode>("starter_preset");
  const [stockIdsText, setStockIdsText] = useState("TW:2727\nTW:2618\nTW:2330");
  const [themeKeysText, setThemeKeysText] = useState("tw-pcb\ntw-ccl");
  const [targetCount, setTargetCount] = useState<10 | 20>(10);
  const [scanId, setScanId] = useState<string | null>(initialScanId);
  const [page, setPage] = useState(1);
  const [agentText, setAgentText] = useState("");
  const [agentChunkIndex, setAgentChunkIndex] = useState(0);
  const [agentTotalChunks, setAgentTotalChunks] = useState(0);
  const [latestManifestText, setLatestManifestText] = useState("");
  const [latestSnapshotId, setLatestSnapshotId] = useState<string | null>(null);
  const [activeSelectionKey, setActiveSelectionKey] = useState<string | null>(null);
  const [keptExistingScan, setKeptExistingScan] = useState(false);
  const [recoveredOperations, setRecoveredOperations] = useState<Record<string, boolean>>({});
  const [membershipText, setMembershipText] = useState("");
  const [membershipVersion, setMembershipVersion] = useState(0);
  const [firstPassJobId, setFirstPassJobId] = useState<string | null>(null);
  const [firstPassProgressText, setFirstPassProgressText] = useState("");
  const [firstPassEvidenceText, setFirstPassEvidenceText] = useState("");
  const [firstPassSnapshotId, setFirstPassSnapshotId] = useState<string | null>(null);
  const [firstPassDigest, setFirstPassDigest] = useState<string | null>(null);
  const [candidateIdsText, setCandidateIdsText] = useState("");
  const [candidateSetText, setCandidateSetText] = useState("");
  const scanRef = useRef<TwResearchScanSnapshot | null>(null);
  const scanIdRef = useRef<string | null>(scanId);
  const createGuard = useRef(false);

  const refreshManifest = (id: string | null) => {
    if (!id) {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["tw-research-manifest", id] });
  };

  const requireScanId = () => {
    if (!scanId) {
      throw new Error("missing scan");
    }
    return scanId;
  };

  const applyScanSnapshot = (next: TwResearchScanSnapshot) => {
    scanRef.current = next;
    queryClient.setQueryData(["tw-research-scan", next.scanId], next);
    queryClient.setQueryData(["tw-research-progress", next.scanId], {
      scanId: next.scanId,
      tradingDay: next.tradingDay,
      universeVersion: next.universeVersion,
      status: next.status,
      qualityStatus: next.qualityStatus ?? "processed",
      cursor: next.cursor,
      enrichCursor: next.enrichCursor ?? 0,
      universeSize: next.universeSize,
      processedCount: next.processedCount,
      definitiveEvaluatedCount: next.definitiveEvaluatedCount ?? 0,
      budgetExhaustedCount: next.budgetExhaustedCount ?? 0,
      sourceIncompleteCount: next.sourceIncompleteCount ?? 0,
      unresolvedCount: next.unresolvedCount ?? 0,
      unprocessedCount: next.unprocessedCount ?? 0,
      remainingCount: next.remainingCount,
      targetCount: next.targetCount,
      dayPassCount: next.dayPassCount,
      qualifiedCount: next.qualifiedCount ?? 0,
      enrichmentsRun: next.enrichmentsRun ?? 0,
      maxEnrichmentsPerRun: next.maxEnrichmentsPerRun ?? 0,
      upstreamMonthFetches: next.upstreamMonthFetches ?? 0,
      maxUpstreamMonthFetches: next.maxUpstreamMonthFetches ?? 0,
      createdAt: next.createdAt,
      updatedAt: next.updatedAt,
      activeSnapshotId: next.activeSnapshotId ?? null,
      workerActivity: workerActivity(next),
      ledgerRevision: researchProgressRevision({
        updatedAt: next.updatedAt,
        status: next.status,
        qualityStatus: next.qualityStatus,
        cursor: next.cursor,
        enrichCursor: next.enrichCursor,
        processedCount: next.processedCount,
        upstreamMonthFetches: next.upstreamMonthFetches,
        enrichmentsRun: next.enrichmentsRun,
        workerActivity: workerActivity(next),
        activeSnapshotId: next.activeSnapshotId,
      }),
      includesLedger: false,
    });
    refreshManifest(next.scanId);
  };

  const reloadDurableScan = async (): Promise<TwResearchScanSnapshot | null> => {
    const id = scanRef.current?.scanId;
    if (!id) return null;
    try {
      const latest = await fetchTwResearchScan(id);
      applyScanSnapshot(latest);
      return latest;
    } catch {
      return null;
    }
  };

  const markRecovered = (operation: string, recovered: boolean) => {
    setRecoveredOperations((current) => ({ ...current, [operation]: recovered }));
  };

  const explicitIds = useMemo(() => splitLines(stockIdsText), [stockIdsText]);
  const themeKeys = useMemo(() => splitLines(themeKeysText), [themeKeysText]);
  const selectionKey = useMemo(
    () =>
      researchSelectionKey({
        mode,
        targetCount,
        stockIds: explicitIds,
        themeKeys,
      }),
    [explicitIds, mode, targetCount, themeKeys],
  );

  useEffect(() => {
    setKeptExistingScan(false);
  }, [selectionKey]);

  const themesQuery = useQuery({
    queryKey: ["themes-for-tw-research"],
    queryFn: fetchThemeOptions,
  });

  const membershipQuery = useQuery({
    queryKey: ["tw-research-membership"],
    queryFn: fetchTwResearchMembership,
    staleTime: 15_000,
    retry: false,
  });

  useEffect(() => {
    if (!membershipQuery.data) return;
    setMembershipText(JSON.stringify(membershipQuery.data, null, 2));
    setMembershipVersion(Number(membershipQuery.data.version ?? 0));
  }, [membershipQuery.data]);

  const firstPassProgressQuery = useQuery({
    queryKey: ["tw-research-first-pass-progress", firstPassJobId],
    queryFn: () => fetchTwResearchFirstPassProgress(firstPassJobId as string),
    enabled: Boolean(firstPassJobId),
    refetchInterval: (query) => {
      const status = String(query.state.data?.status ?? "");
      return ["pending", "running", "partial", "deferred"].includes(status) ? 4_000 : false;
    },
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    if (!firstPassProgressQuery.data) return;
    setFirstPassProgressText(JSON.stringify(firstPassProgressQuery.data, null, 2));
    const snapshotId = firstPassProgressQuery.data.activeSnapshotId;
    if (typeof snapshotId === "string" && snapshotId) {
      setFirstPassSnapshotId(snapshotId);
    }
  }, [firstPassProgressQuery.data]);

  const twThemeHint = useMemo(() => {
    const keys = (themesQuery.data ?? [])
      .map((theme) => theme.key)
      .filter((key) => key.startsWith("tw-"));
    return keys.length ? keys.join(", ") : "tw-pcb, tw-ccl, …";
  }, [themesQuery.data]);

  const listQuery = useQuery({
    queryKey: ["tw-research-scans"],
    queryFn: () => fetchTwResearchScans(),
    staleTime: 15_000,
    refetchInterval: researchScanListRefetchInterval(),
    refetchIntervalInBackground: false,
  });

  const progressQuery = useQuery({
    queryKey: ["tw-research-progress", scanId],
    queryFn: () => fetchTwResearchScanProgress(scanId as string),
    enabled: Boolean(scanId),
    staleTime: 5_000,
    refetchInterval: (query) => researchScanRefetchInterval(query.state.data?.status),
    refetchIntervalInBackground: false,
  });

  const scanQuery = useQuery({
    queryKey: ["tw-research-scan", scanId],
    queryFn: () => fetchTwResearchScan(scanId as string),
    enabled: Boolean(scanId),
    staleTime: 30_000,
    // Full ledger is loaded on selection / revision change, not every poll tick.
    refetchInterval: false,
    refetchIntervalInBackground: false,
  });

  const scan = scanQuery.data?.scanId === scanId ? scanQuery.data : null;
  const progress = progressQuery.data?.scanId === scanId ? progressQuery.data : null;
  const displayStatus = progress?.status ?? scan?.status ?? "";
  const displayProcessed = progress?.processedCount ?? scan?.processedCount ?? 0;
  const displayUniverse = progress?.universeSize ?? scan?.universeSize ?? 0;
  const displayRemaining = progress?.remainingCount ?? scan?.remainingCount ?? 0;
  const displayDayPass = progress?.dayPassCount ?? scan?.dayPassCount ?? 0;
  const displayQualified = progress?.qualifiedCount ?? scan?.qualifiedCount ?? 0;
  const displayTarget = progress?.targetCount ?? scan?.targetCount ?? 0;
  const displayWorker =
    progress?.workerActivity ?? (scan ? workerActivity(scan) : "unknown");
  const displayBudgetUsed = progress?.upstreamMonthFetches ?? scan?.upstreamMonthFetches ?? 0;
  const displayBudgetMax = progress?.maxUpstreamMonthFetches ?? scan?.maxUpstreamMonthFetches ?? 0;
  const displayEnrichRun = progress?.enrichmentsRun ?? scan?.enrichmentsRun ?? 0;
  const displayEnrichMax = progress?.maxEnrichmentsPerRun ?? scan?.maxEnrichmentsPerRun ?? 0;
  const displayEnrichCursor = progress?.enrichCursor ?? scan?.enrichCursor ?? 0;
  const displayQuality = progress?.qualityStatus ?? scan?.qualityStatus;
  const displayUpdatedAt = progress?.updatedAt ?? scan?.updatedAt;
  const displayDefinitive =
    progress?.definitiveEvaluatedCount ?? scan?.definitiveEvaluatedCount ?? 0;
  const displayBudgetExhausted =
    progress?.budgetExhaustedCount ?? scan?.budgetExhaustedCount ?? 0;
  const displayIncomplete =
    progress?.sourceIncompleteCount ?? scan?.sourceIncompleteCount ?? 0;
  const displayUnresolved = progress?.unresolvedCount ?? scan?.unresolvedCount ?? 0;
  const displayUnprocessed = progress?.unprocessedCount ?? scan?.unprocessedCount ?? 0;

  useEffect(() => {
    if (scan) scanRef.current = scan;
  }, [scan]);

  useEffect(() => {
    scanIdRef.current = scanId;
  }, [scanId]);

  useEffect(() => {
    setAgentText("");
    setAgentChunkIndex(0);
    setAgentTotalChunks(0);
    setPage(1);
  }, [scanId]);

  useEffect(() => {
    if (!scanId || !progress?.ledgerRevision) return;
    const currentRevision = researchProgressRevision({
      updatedAt: scan?.updatedAt,
      status: scan?.status,
      qualityStatus: scan?.qualityStatus,
      cursor: scan?.cursor,
      enrichCursor: scan?.enrichCursor,
      processedCount: scan?.processedCount,
      upstreamMonthFetches: scan?.upstreamMonthFetches,
      enrichmentsRun: scan?.enrichmentsRun,
      workerActivity: scan ? workerActivity(scan) : null,
      activeSnapshotId: scan?.activeSnapshotId,
    });
    if (currentRevision === progress.ledgerRevision) return;
    void queryClient.invalidateQueries({ queryKey: ["tw-research-scan", scanId] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-manifest", scanId] });
  }, [progress, queryClient, scan, scanId]);

  useEffect(() => {
    // First visit only: pick the newest persisted scan. Never steal a deliberate selection.
    if (scanId || !listQuery.data?.scans.length) return;
    setScanId(listQuery.data.scans[0]?.scanId ?? null);
  }, [listQuery.data, scanId]);

  useEffect(() => {
    if (!scanId || typeof window === "undefined") return;
    const current = new URLSearchParams(window.location.search).get("scan");
    if (current === scanId) return;
    router.replace(`/research/tw?scan=${encodeURIComponent(scanId)}`, { scroll: false });
  }, [router, scanId]);

  const manifestQuery = useQuery({
    queryKey: ["tw-research-manifest", scanId],
    queryFn: () => fetchTwResearchExportManifest(scanId as string),
    enabled: Boolean(scanId),
  });

  const createMutation = useMutation({
    mutationFn: async (key: string) => {
      if (mode === "stock_ids") {
        await resolveTwResearchUniverse({ mode, stockIds: explicitIds });
      } else if (mode === "theme_keys") {
        await resolveTwResearchUniverse({ mode, themeKeys });
      }
      const created = await createTwResearchScan({
        mode,
        stockIds: mode === "stock_ids" ? explicitIds : [],
        themeKeys: mode === "theme_keys" ? themeKeys : [],
        targetCount,
      });
      return { created, key };
    },
    onSuccess: ({ created, key }) => {
      setScanId(created.scanId);
      applyScanSnapshot(created);
      setActiveSelectionKey(key);
      setKeptExistingScan(false);
      void queryClient.invalidateQueries({ queryKey: ["tw-research-scans"] });
    },
    onSettled: () => {
      createGuard.current = false;
    },
  });

  const previewMembershipMutation = useMutation({
    mutationFn: async () =>
      previewTwResearchMembership({
        themeKeys,
        extraStockIds: explicitIds,
      }),
    onSuccess: (membership) => {
      setMembershipText(JSON.stringify(membership, null, 2));
    },
  });

  const saveMembershipMutation = useMutation({
    mutationFn: async () =>
      saveTwResearchMembership({
        expectedVersion: membershipVersion,
        themeKeys,
        extraStockIds: explicitIds,
      }),
    onSuccess: (membership) => {
      setMembershipText(JSON.stringify(membership, null, 2));
      setMembershipVersion(Number(membership.version ?? 0));
      void queryClient.invalidateQueries({ queryKey: ["tw-research-membership"] });
    },
  });

  const resetMembershipMutation = useMutation({
    mutationFn: async () =>
      saveTwResearchMembership({
        expectedVersion: membershipVersion,
        resetToStarter: true,
      }),
    onSuccess: (membership) => {
      setMembershipText(JSON.stringify(membership, null, 2));
      setMembershipVersion(Number(membership.version ?? 0));
      void queryClient.invalidateQueries({ queryKey: ["tw-research-membership"] });
    },
  });

  const startFirstPassMutation = useMutation({
    mutationFn: async () => createTwResearchFirstPassJob({ useMembership: true }),
    onSuccess: (job) => {
      setFirstPassJobId(String(job.jobId ?? ""));
      setFirstPassProgressText(JSON.stringify(job, null, 2));
    },
  });

  const firstPassBatchMutation = useMutation({
    mutationFn: async () => {
      if (!firstPassJobId) throw new Error("missing_first_pass_job");
      return runTwResearchFirstPassBatch(firstPassJobId, 10);
    },
    onSuccess: (job) => {
      setFirstPassProgressText(JSON.stringify(job, null, 2));
      if (typeof job.activeSnapshotId === "string" && job.activeSnapshotId) {
        setFirstPassSnapshotId(job.activeSnapshotId);
      }
      void queryClient.invalidateQueries({
        queryKey: ["tw-research-first-pass-progress", firstPassJobId],
      });
    },
  });

  const loadFirstPassEvidenceMutation = useMutation({
    mutationFn: async () => {
      if (!firstPassJobId) throw new Error("missing_first_pass_job");
      return fetchTwResearchFirstPassSymbols(firstPassJobId, { offset: 0, limit: 25 });
    },
    onSuccess: (result) => {
      setFirstPassEvidenceText(JSON.stringify(result, null, 2));
    },
  });

  const loadFirstPassManifestMutation = useMutation({
    mutationFn: async () => {
      if (!firstPassSnapshotId) throw new Error("missing_first_pass_snapshot");
      const manifest = await fetchTwResearchFirstPassManifest(firstPassSnapshotId);
      const chunk = await fetchTwResearchFirstPassChunk(firstPassSnapshotId, 0);
      setFirstPassDigest(
        typeof manifest.contentDigest === "string" ? manifest.contentDigest : null,
      );
      return { manifest, chunk };
    },
    onSuccess: (payload) => {
      setFirstPassEvidenceText(JSON.stringify(payload, null, 2));
    },
  });

  const saveCandidateSetMutation = useMutation({
    mutationFn: async () => {
      if (!firstPassSnapshotId || !firstPassDigest) {
        throw new Error("missing_first_pass_snapshot_or_digest");
      }
      return submitTwResearchCandidateSet({
        firstPassSnapshotId,
        firstPassDigest,
        stockIds: splitLines(candidateIdsText),
        origin: "manual",
        expectedVersion: 0,
      });
    },
    onSuccess: (candidateSet) => {
      setCandidateSetText(JSON.stringify(candidateSet, null, 2));
    },
  });

  const batchMutation = useMutation({
    mutationFn: async () => runTwResearchBatch(requireScanId(), 10),
    onMutate: () => markRecovered("batch", false),
    onSuccess: applyScanSnapshot,
    onError: async () => {
      const before = scanProgressKey(scanRef.current);
      const latest = await reloadDurableScan();
      markRecovered("batch", recoveredAfterLostMutation(before, latest));
    },
  });

  const resumeMutation = useMutation({
    mutationFn: async () => resumeTwResearchBudgetWindow(requireScanId()),
    onSuccess: (next) => {
      applyScanSnapshot(next);
      void queryClient.invalidateQueries({ queryKey: ["tw-research-progress", next.scanId] });
      void queryClient.invalidateQueries({ queryKey: ["tw-research-scans"] });
    },
  });

  const enrichMutation = useMutation({
    mutationFn: async () => runTwResearchEnrichmentBatch(requireScanId(), 10),
    onMutate: () => markRecovered("enrich", false),
    onSuccess: applyScanSnapshot,
    onError: async () => {
      const before = scanProgressKey(scanRef.current);
      const latest = await reloadDurableScan();
      markRecovered("enrich", recoveredAfterLostMutation(before, latest));
    },
  });

  const refreshMutation = useMutation({
    mutationFn: async () => fetchTwResearchScan(requireScanId()),
    onSuccess: applyScanSnapshot,
  });

  const agentMutation = useMutation({
    mutationFn: async (chunkIndex: number) => {
      const id = requireScanId();
      const payload = await fetchTwResearchBrowserText(id, chunkIndex);
      return { id, payload, chunkIndex };
    },
    onSuccess: ({ id, payload, chunkIndex }) => {
      if (scanIdRef.current !== id) return;
      setAgentChunkIndex(chunkIndex);
      const continuation = payload.continuation as
        | { totalChunks?: number; nextChunkIndex?: number | null }
        | undefined;
      setAgentTotalChunks(Number(continuation?.totalChunks ?? 0));
      setAgentText(JSON.stringify(payload, null, 2));
    },
  });

  const latestManifestMutation = useMutation({
    mutationFn: async () =>
      fetchTwResearchLatestManifest({
        universeVersion: scan?.universeVersion,
        tradingDay: scan?.tradingDay,
      }),
    onSuccess: (manifest) => {
      setLatestManifestText(JSON.stringify(manifest, null, 2));
      setLatestSnapshotId(
        typeof manifest.snapshotId === "string" ? manifest.snapshotId : null,
      );
    },
  });

  const latestChunkMutation = useMutation({
    mutationFn: async () => {
      if (!latestSnapshotId) {
        throw new Error("missing snapshot");
      }
      return fetchTwResearchSnapshotChunk(latestSnapshotId, 0, "ledger");
    },
    onSuccess: (chunk) => {
      setLatestManifestText(JSON.stringify(chunk, null, 2));
    },
  });

  const ledger = (scan?.ledger ?? []) as Array<Record<string, unknown>>;
  const pageRows = ledger.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const busy =
    createMutation.isPending ||
    batchMutation.isPending ||
    resumeMutation.isPending ||
    enrichMutation.isPending ||
    refreshMutation.isPending ||
    agentMutation.isPending ||
    latestManifestMutation.isPending ||
    latestChunkMutation.isPending;
  const requestedTradingDay = taipeiTradingDay();
  const operationErrors: Array<{ key: string; label: TranslationKey; error: unknown }> = [
    {
      key: "list",
      label: "research.tw.errorList",
      error: listQuery.isFetching ? null : listQuery.error,
    },
    {
      key: "create",
      label: "research.tw.errorCreate",
      error: createMutation.isPending ? null : createMutation.error,
    },
    {
      key: "batch",
      label: "research.tw.errorBatch",
      error: batchMutation.isPending || recoveredOperations.batch ? null : batchMutation.error,
    },
    {
      key: "resume",
      label: "research.tw.errorResume",
      error: resumeMutation.isPending ? null : resumeMutation.error,
    },
    {
      key: "enrich",
      label: "research.tw.errorEnrich",
      error: enrichMutation.isPending || recoveredOperations.enrich ? null : enrichMutation.error,
    },
    {
      key: "refresh",
      label: "research.tw.errorRefresh",
      error: refreshMutation.isPending ? null : refreshMutation.error,
    },
    {
      key: "agent",
      label: "research.tw.errorAgent",
      error: agentMutation.isPending ? null : agentMutation.error,
    },
    {
      key: "latest-manifest",
      label: "research.tw.errorLatestManifest",
      error: latestManifestMutation.isPending ? null : latestManifestMutation.error,
    },
    {
      key: "latest-chunk",
      label: "research.tw.errorLatestChunk",
      error: latestChunkMutation.isPending ? null : latestChunkMutation.error,
    },
    {
      key: "manifest",
      label: "research.tw.errorManifest",
      error: manifestQuery.isFetching ? null : manifestQuery.error,
    },
  ];

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column lg={16} md={8} sm={4}>
        <Stack gap={6}>
          <div>
            <p className="cds--label-01">{t("research.tw.eyebrow")}</p>
            <h1 className="cds--productive-heading-04">{t("research.tw.title")}</h1>
            <p className="cds--body-compact-01">{t("research.tw.subtitle")}</p>
          </div>

          {operationErrors.map((failure) => {
            if (!failure.error) return null;
            const apiError = toApiError(failure.error);
            return (
              <div key={failure.key} data-testid={`tw-research-error-${failure.key}`}>
                <RequestStateNotice
                  kind={errorToNoticeKind(apiError)}
                  scopeLabel={t(failure.label)}
                  status={apiError.status}
                  detail={describeResearchFailure(failure.error)}
                />
              </div>
            );
          })}

          <Tile data-testid="tw-research-fundamentals">
            <Stack gap={4}>
              <div>
                <h2 className="cds--productive-heading-03">{t("research.tw.fundamentalsTitle")}</h2>
                <p className="cds--helper-text-01">{t("research.tw.fundamentalsNote")}</p>
                <p className="cds--helper-text-01">{t("research.tw.stageHint")}</p>
              </div>
              <Stack gap={3} orientation="horizontal">
                <Button
                  kind="tertiary"
                  size="sm"
                  disabled={membershipQuery.isFetching}
                  onClick={() => void membershipQuery.refetch()}
                >
                  {t("research.tw.loadMembership")}
                </Button>
                <Button
                  kind="tertiary"
                  size="sm"
                  disabled={previewMembershipMutation.isPending}
                  onClick={() => previewMembershipMutation.mutate()}
                >
                  {t("research.tw.previewMembership")}
                </Button>
                <Button
                  kind="primary"
                  size="sm"
                  disabled={saveMembershipMutation.isPending}
                  onClick={() => saveMembershipMutation.mutate()}
                >
                  {t("research.tw.saveMembership")}
                </Button>
                <Button
                  kind="danger--tertiary"
                  size="sm"
                  disabled={resetMembershipMutation.isPending}
                  onClick={() => resetMembershipMutation.mutate()}
                >
                  {t("research.tw.resetMembership")}
                </Button>
              </Stack>
              <Stack gap={3} orientation="horizontal">
                <Button
                  kind="primary"
                  size="sm"
                  disabled={startFirstPassMutation.isPending}
                  onClick={() => startFirstPassMutation.mutate()}
                >
                  {t("research.tw.startFirstPass")}
                </Button>
                <Button
                  kind="secondary"
                  size="sm"
                  disabled={!firstPassJobId || firstPassBatchMutation.isPending}
                  onClick={() => firstPassBatchMutation.mutate()}
                >
                  {t("research.tw.runFirstPassBatch")}
                </Button>
                <Button
                  kind="tertiary"
                  size="sm"
                  disabled={!firstPassJobId || loadFirstPassEvidenceMutation.isPending}
                  onClick={() => loadFirstPassEvidenceMutation.mutate()}
                >
                  {t("research.tw.loadFirstPassEvidence")}
                </Button>
                <Button
                  kind="tertiary"
                  size="sm"
                  disabled={!firstPassSnapshotId || loadFirstPassManifestMutation.isPending}
                  onClick={() => loadFirstPassManifestMutation.mutate()}
                >
                  {t("research.tw.loadFirstPassManifest")}
                </Button>
              </Stack>
              <TextArea
                id="tw-research-membership-json"
                labelText={t("research.tw.membershipJson")}
                value={membershipText}
                readOnly
                rows={6}
              />
              <TextArea
                id="tw-research-first-pass-progress"
                labelText={t("research.tw.firstPassProgress")}
                value={firstPassProgressText}
                readOnly
                rows={5}
              />
              <TextArea
                id="tw-research-first-pass-evidence"
                labelText={t("research.tw.firstPassEvidence")}
                value={firstPassEvidenceText}
                readOnly
                rows={6}
              />
              <TextArea
                id="tw-research-candidate-ids"
                labelText={t("research.tw.candidateIds")}
                value={candidateIdsText}
                onChange={(event) => setCandidateIdsText(event.target.value)}
                rows={3}
              />
              <Button
                kind="primary"
                size="sm"
                disabled={
                  !firstPassSnapshotId ||
                  !firstPassDigest ||
                  saveCandidateSetMutation.isPending
                }
                onClick={() => saveCandidateSetMutation.mutate()}
              >
                {t("research.tw.saveCandidateSet")}
              </Button>
              {candidateSetText ? (
                <TextArea
                  id="tw-research-candidate-set-json"
                  labelText={t("research.tw.saveCandidateSet")}
                  value={candidateSetText}
                  readOnly
                  rows={4}
                />
              ) : null}
            </Stack>
          </Tile>

          <Tile>
            <Stack gap={4}>
              <Select
                id="tw-research-mode"
                labelText={t("research.tw.universeMode")}
                value={mode}
                onChange={(event) => setMode(event.target.value as TwResearchUniverseMode)}
              >
                <SelectItem value="starter_preset" text={t("research.tw.modePreset")} />
                <SelectItem value="theme_keys" text={t("research.tw.modeThemes")} />
                <SelectItem value="stock_ids" text={t("research.tw.modeIds")} />
              </Select>
              {mode === "stock_ids" ? (
                <TextArea
                  id="tw-research-stock-ids"
                  labelText={t("research.tw.stockIds")}
                  value={stockIdsText}
                  onChange={(event) => setStockIdsText(event.target.value)}
                  rows={4}
                />
              ) : null}
              {mode === "theme_keys" ? (
                <Stack gap={2}>
                  <TextArea
                    id="tw-research-theme-keys"
                    labelText={t("research.tw.themeKeys")}
                    value={themeKeysText}
                    onChange={(event) => setThemeKeysText(event.target.value)}
                    rows={4}
                  />
                  <p className="cds--helper-text-01">
                    {t("research.tw.themeKeysHint", { keys: twThemeHint })}
                  </p>
                </Stack>
              ) : null}
              {listQuery.data?.scans.length || scanId ? (
                <Select
                  id="tw-research-history"
                  data-testid="tw-research-history"
                  labelText={t("research.tw.historyLabel")}
                  value={scanId ?? ""}
                  onChange={(event) => {
                    const next = event.target.value;
                    if (next) setScanId(next);
                  }}
                >
                  {(listQuery.data?.scans ?? []).map((item) => (
                    <SelectItem
                      key={item.scanId}
                      value={item.scanId}
                      text={`${item.tradingDay} · ${item.status} · ${item.cursor}/${item.universeSize}`}
                    />
                  ))}
                  {scanId &&
                  !(listQuery.data?.scans ?? []).some((item) => item.scanId === scanId) ? (
                    <SelectItem value={scanId} text={scanId} />
                  ) : null}
                </Select>
              ) : null}
              <Select
                id="tw-research-target"
                labelText={t("research.tw.targetCount")}
                value={String(targetCount)}
                onChange={(event) => setTargetCount(Number(event.target.value) as 10 | 20)}
              >
                <SelectItem value="10" text="10" />
                <SelectItem value="20" text="20" />
              </Select>
              <Stack gap={3} orientation="horizontal">
                {(
                  [
                    {
                      key: "create",
                      kind: "primary" as const,
                      label: "research.tw.createScan" as const,
                      disabled: busy,
                      onClick: () => {
                        if (createGuard.current || createMutation.isPending) return;
                        const persisted = (listQuery.data?.scans ?? []).find(
                          (item) =>
                            ACTIVE_SCAN_STATUSES.has(item.status) &&
                            item.tradingDay === requestedTradingDay &&
                            researchSelectionKey({
                              mode: item.selectorMode,
                              targetCount: item.targetCount,
                              stockIds: item.stockIds,
                              themeKeys: item.themeKeys,
                            }) === selectionKey,
                        );
                        if (persisted) {
                          setScanId(persisted.scanId);
                          setActiveSelectionKey(selectionKey);
                          setKeptExistingScan(true);
                          return;
                        }
                        if (scanId && selectionKey === activeSelectionKey) {
                          setKeptExistingScan(true);
                          return;
                        }
                        createGuard.current = true;
                        setKeptExistingScan(false);
                        createMutation.mutate(selectionKey);
                      },
                    },
                    {
                      key: "batch",
                      kind: "secondary" as const,
                      label: "research.tw.runBatch" as const,
                      disabled:
                        busy ||
                        !scanId ||
                        (displayStatus !== "" && !ACTIVE_SCAN_STATUSES.has(displayStatus)),
                      onClick: () => batchMutation.mutate(),
                    },
                    {
                      key: "resume",
                      kind: "tertiary" as const,
                      label: "research.tw.resumeBudget" as const,
                      disabled: busy || !scanId || displayStatus !== "budget_exhausted",
                      onClick: () => resumeMutation.mutate(),
                    },
                    {
                      key: "enrich",
                      kind: "tertiary" as const,
                      label: "research.tw.runEnrichment" as const,
                      disabled: busy || !scanId,
                      onClick: () => enrichMutation.mutate(),
                    },
                    {
                      key: "refresh",
                      kind: "ghost" as const,
                      label: "research.tw.refresh" as const,
                      disabled: busy || !scanId,
                      onClick: () => refreshMutation.mutate(),
                    },
                  ]
                ).map((action) => (
                  <Button
                    key={action.key}
                    size="md"
                    kind={action.kind}
                    disabled={action.disabled}
                    data-testid={`tw-research-${action.key}`}
                    onClick={action.onClick}
                  >
                    {t(action.label)}
                  </Button>
                ))}
              </Stack>
              {keptExistingScan ? (
                <p className="cds--helper-text-01" data-testid="tw-research-existing-scan">
                  {t("research.tw.existingScanKept")}
                </p>
              ) : null}
              {displayStatus === "budget_exhausted" ? (
                <p className="cds--helper-text-01" data-testid="tw-research-budget-exhausted">
                  {t("research.tw.budgetExhausted")}
                </p>
              ) : null}
              {busy ? <InlineLoading description={t("research.tw.working")} /> : null}
            </Stack>
          </Tile>

          {(scanQuery.isError || progressQuery.isError) && scan ? (
            <div data-testid="tw-research-stale">
              <RequestStateNotice
                kind="stale"
                scopeLabel={t("research.tw.progressTitle")}
              />
            </div>
          ) : null}
          {scanQuery.isError && !scan ? (
            <div data-testid="tw-research-error-scan">
              <RequestStateNotice
                kind={errorToNoticeKind(toApiError(scanQuery.error))}
                scopeLabel={t("research.tw.errorScan")}
                status={toApiError(scanQuery.error).status}
                detail={describeResearchFailure(scanQuery.error)}
              />
            </div>
          ) : null}

          {scan ? (
            <Tile data-testid="tw-research-progress">
              <Stack gap={3}>
                <TextInput
                  id="tw-research-scan-id"
                  labelText={t("research.tw.scanId")}
                  value={scan.scanId}
                  readOnly
                />
                <p className="cds--body-compact-01">
                  {t("research.tw.statusLine", {
                    status: displayStatus,
                    processed: displayProcessed,
                    universe: displayUniverse,
                    remaining: displayRemaining,
                    dayPass: displayDayPass,
                    qualified: displayQualified,
                    target: displayTarget,
                  })}
                </p>
                <p className="cds--body-compact-01" data-testid="tw-research-worker">
                  {t(workerActivityLabelKey(displayWorker))}
                </p>
                <p className="cds--helper-text-01">
                  {t("research.tw.budgetLine", {
                    used: displayBudgetUsed,
                    max: displayBudgetMax,
                  })}
                </p>
                <p className="cds--helper-text-01" data-testid="tw-research-evaluation">
                  {t("research.tw.evaluationLine", {
                    definitive: displayDefinitive,
                    budget: displayBudgetExhausted,
                    incomplete: displayIncomplete,
                    unresolved: displayUnresolved,
                    unprocessed: displayUnprocessed,
                    processed: displayProcessed,
                  })}
                </p>
                <p className="cds--helper-text-01">
                  {t("research.tw.enrichmentLine", {
                    run: displayEnrichRun,
                    max: displayEnrichMax,
                    cursor: displayEnrichCursor,
                  })}
                </p>
                {displayQuality && displayQuality !== "verified_ready" ? (
                  <p className="cds--helper-text-01" data-testid="tw-research-quality">
                    {t("research.tw.notVerified", { quality: displayQuality })}
                  </p>
                ) : null}
                {displayQuality === "verified_ready" ? (
                  <p className="cds--helper-text-01" data-testid="tw-research-quality">
                    {t("research.tw.verifiedExport")}
                  </p>
                ) : null}
                {displayUpdatedAt ? (
                  <p className="cds--helper-text-01">
                    {t("research.tw.updatedAt", { time: displayUpdatedAt })}
                  </p>
                ) : null}
                {ledger.some((row) => row.rejection_reason || row.readiness === "source_incomplete") ? (
                  <div data-testid="tw-research-unresolved">
                    <p className="cds--label-01">{t("research.tw.unresolvedTitle")}</p>
                    {ledger
                      .filter((row) => row.rejection_reason || row.readiness === "source_incomplete")
                      .slice(0, 20)
                      .map((row) => (
                        <p key={displayText(row.stock_id)} className="cds--helper-text-01">
                          {displayText(row.stock_id)} ·{" "}
                          {displayText(row.rejection_reason || row.readiness || "")}
                        </p>
                      ))}
                  </div>
                ) : null}
                <p className="cds--helper-text-01">{t("research.tw.targetNote")}</p>
                {manifestQuery.data ? (
                  <pre className="cds--body-compact-01" style={{ whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(manifestQuery.data, null, 2)}
                  </pre>
                ) : null}
              </Stack>
            </Tile>
          ) : null}

          {scan ? (
            <TableContainer title={t("research.tw.ledgerTitle")}>
              <Table size="sm">
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("research.tw.colStock")}</TableHeader>
                    <TableHeader>{t("research.tw.colName")}</TableHeader>
                    <TableHeader>{t("research.tw.colStage")}</TableHeader>
                    <TableHeader>{t("research.tw.colDaily")}</TableHeader>
                    <TableHeader>{t("research.tw.colMonthly")}</TableHeader>
                    <TableHeader>{t("research.tw.colThemes")}</TableHeader>
                    <TableHeader>{t("research.tw.colReason")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {pageRows.map((row) => {
                    const daily = (row.daily as Record<string, unknown> | undefined) || {};
                    const monthly = (row.monthly as Record<string, unknown> | undefined) || {};
                    const themes = (row.theme_keys as string[] | undefined) || [];
                    const stockName =
                      displayText(row.stock_name) ||
                      displayText(row.stockName) ||
                      "";
                    const nameUnavailable =
                      row.name_status === "unavailable" ||
                      row.nameStatus === "unavailable" ||
                      !stockName;
                    return (
                      <TableRow key={displayText(row.stock_id)}>
                        <TableCell>{displayText(row.stock_id)}</TableCell>
                        <TableCell>
                          {nameUnavailable
                            ? t("research.tw.nameUnavailable")
                            : stockName}
                        </TableCell>
                        <TableCell>{displayText(row.stage)}</TableCell>
                        <TableCell>{displayText(daily.passStatus)}</TableCell>
                        <TableCell>{displayText(monthly.passStatus)}</TableCell>
                        <TableCell>
                          {themes.length ? themes.join(", ") : t("research.tw.unclassified")}
                        </TableCell>
                        <TableCell>{displayText(row.rejection_reason)}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                pageSizes={[PAGE_SIZE]}
                totalItems={ledger.length}
                onChange={({ page: nextPage }) => setPage(nextPage)}
              />
            </TableContainer>
          ) : null}

          <Tile data-testid="tw-research-latest-verified">
            <Stack gap={4}>
              <div>
                <h2 className="cds--productive-heading-02">
                  {t("research.tw.latestVerifiedTitle")}
                </h2>
                <p className="cds--helper-text-01">{t("research.tw.latestVerifiedNote")}</p>
              </div>
              <Stack gap={3} orientation="horizontal">
                <Button
                  size="md"
                  kind="secondary"
                  disabled={busy}
                  data-testid="tw-research-load-latest-manifest"
                  onClick={() => latestManifestMutation.mutate()}
                >
                  {t("research.tw.loadLatestManifest")}
                </Button>
                <Button
                  size="md"
                  kind="tertiary"
                  disabled={busy || !latestSnapshotId}
                  data-testid="tw-research-load-latest-chunk"
                  onClick={() => latestChunkMutation.mutate()}
                >
                  {t("research.tw.loadLatestChunk")}
                </Button>
              </Stack>
              <TextArea
                id="tw-research-latest-manifest-json"
                data-testid="tw-research-latest-manifest-json"
                labelText={t("research.tw.latestManifestJson")}
                value={latestManifestText}
                readOnly
                rows={12}
              />
            </Stack>
          </Tile>

          {scanId ? (
            <Tile>
              <Stack gap={4}>
                <Stack gap={3} orientation="horizontal">
                  <Button
                    size="md"
                    kind="secondary"
                    disabled={busy}
                    onClick={() => agentMutation.mutate(0)}
                  >
                    {t("research.tw.loadAgentJson")}
                  </Button>
                  <Button
                    size="md"
                    kind="ghost"
                    disabled={busy || agentTotalChunks <= 0 || agentChunkIndex <= 0}
                    onClick={() => agentMutation.mutate(Math.max(0, agentChunkIndex - 1))}
                  >
                    {t("research.tw.prevChunk")}
                  </Button>
                  <Button
                    size="md"
                    kind="ghost"
                    disabled={
                      busy ||
                      agentTotalChunks <= 0 ||
                      agentChunkIndex + 1 >= agentTotalChunks
                    }
                    onClick={() => agentMutation.mutate(agentChunkIndex + 1)}
                  >
                    {t("research.tw.nextChunk")}
                  </Button>
                  <Button
                    size="md"
                    kind="tertiary"
                    disabled={busy}
                    onClick={async () => {
                      const id = scanId;
                      if (!id) {
                        return;
                      }
                      const chunk = await fetchTwResearchExportChunk(id, agentChunkIndex);
                      if (scanIdRef.current !== id) return;
                      setAgentText(JSON.stringify(chunk, null, 2));
                    }}
                  >
                    {t("research.tw.loadChunkOnly")}
                  </Button>
                  <Button size="md" kind="tertiary" disabled={busy}>
                    <a href={twResearchCsvUrl(scanId)}>{t("research.tw.downloadCsv")}</a>
                  </Button>
                </Stack>
                <p className="cds--helper-text-01">
                  {t("research.tw.agentChunkStatus", {
                    index: agentChunkIndex,
                    total: agentTotalChunks || "—",
                  })}
                </p>
                <p className="cds--helper-text-01">{t("research.tw.agentHandoffNote")}</p>
                <TextArea
                  id="tw-research-agent-json"
                  labelText={t("research.tw.agentJson")}
                  value={agentText}
                  readOnly
                  rows={16}
                />
              </Stack>
            </Tile>
          ) : null}
        </Stack>
      </Column>
    </Grid>
  );
}
