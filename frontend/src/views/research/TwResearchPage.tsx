"use client";

import {
  Button,
  Column,
  ComposedModal,
  Grid,
  InlineLoading,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Pagination,
  Select,
  SelectItem,
  Stack,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
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
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage, type TranslationKey } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { toApiError } from "../../services/api/client";
import {
  ChipEvidenceCard,
  type ChipEvidenceRow,
} from "./ChipEvidenceCard";
import {
  AdvancedDetailsDisclosure,
  ResearchInvestorSummary,
  type BatchResultView,
} from "./ResearchInvestorSummary";
import {
  researchErrorCopy,
  researchErrorRawDetail,
} from "./researchErrorCopy";
import {
  jobHistoryOptionLabel,
  normalizeFailureClass,
  technicalScanPlainStatusKey,
  technicalScanQualityPlainKey,
  type FirstPassProgressLike,
} from "./researchStatusCopy";
import {
  advanceTwResearchRunGate,
  archiveTwResearchFirstPassJob,
  cancelTwResearchFirstPassJob,
  createTwResearchFirstPassJob,
  createTwResearchScan,
  createTwResearchTechnicalScanFromCandidateSet,
  deleteTwResearchFirstPassJob,
  deleteTwResearchRunCurrent,
  fetchThemeOptions,
  fetchTwResearchBrowserText,
  fetchTwResearchCandidateSet,
  fetchTwResearchExportChunk,
  fetchTwResearchExportManifest,
  fetchTwResearchFirstPassChipEvidence,
  fetchTwResearchFirstPassChunk,
  fetchTwResearchFirstPassDeletionPreview,
  fetchTwResearchFirstPassDeletionStatus,
  fetchTwResearchRunDeletionStatus,
  fetchTwResearchFirstPassJob,
  fetchTwResearchFirstPassManifest,
  fetchTwResearchFirstPassProgress,
  fetchTwResearchFirstPassSnapshot,
  fetchTwResearchFirstPassSymbols,
  fetchTwResearchLatestCandidateSet,
  fetchTwResearchLatestManifest,
  fetchTwResearchMembership,
  fetchTwResearchReportEvidenceExampleZhTw,
  fetchTwResearchRunCurrent,
  fetchTwResearchRunDeletionPreview,
  fetchTwResearchScan,
  fetchTwResearchScanProgress,
  fetchTwResearchScans,
  fetchTwResearchSnapshotChunk,
  listTwResearchFirstPassJobs,
  unarchiveTwResearchFirstPassJob,
  postTwResearchReportEvidence,
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
  startTwResearchRun,
  submitTwResearchCandidateSet,
  twResearchCsvUrl,
  type TwResearchRun,
  type TwResearchScanSnapshot,
  type TwResearchUniverseMode,
} from "../../services/api/twResearch";

function researchPhaseLabelKey(phase: string | undefined | null): TranslationKey {
  switch (phase) {
    case "phase1_first_pass":
      return "research.tw.phase1";
    case "awaiting_analyst_candidate_selection":
      return "research.tw.phaseAwaitingCandidates";
    case "phase2_technical":
      return "research.tw.phase2Technical";
    case "awaiting_analyst_report_decision":
    case "complete_evidence_ready":
      return "research.tw.phaseAwaitingReport";
    default:
      return "research.tw.phaseIdle";
  }
}

const PAGE_SIZE = 25;

/** Traditional Chinese name + canonical id; never invent missing names. */
export function formatTwStockLabel(
  stockId: string,
  stockName?: string | null,
  nameStatus?: string | null,
  unavailableLabel = "名稱不可用",
): string {
  const shortId = stockId.includes(":") ? stockId.split(":").slice(1).join(":") : stockId;
  const name = (stockName ?? "").trim();
  if (name && nameStatus !== "unavailable") {
    return `${name} (${shortId})`;
  }
  return `${unavailableLabel} (${shortId})`;
}

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
  const { t, language } = useLanguage();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<TwResearchUniverseMode>("starter_preset");
  const [stockIdsText, setStockIdsText] = useState("TW:2727\nTW:2618\nTW:2330");
  const [themeKeysText, setThemeKeysText] = useState("tw-pcb\ntw-ccl");
  const [targetCount, setTargetCount] = useState<10 | 20>(10);
  const [scanId, setScanId] = useState<string | null>(initialScanId);
  const [page, setPage] = useState(1);
  const [watchlistPage, setWatchlistPage] = useState(1);
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
  const [membershipThemeKeys, setMembershipThemeKeys] = useState<string[]>([]);
  const [disabledThemeKeys, setDisabledThemeKeys] = useState<string[]>([]);
  const [sectorAdditions, setSectorAdditions] = useState<Record<string, string[]>>({});
  const [sectorRemovals, setSectorRemovals] = useState<Record<string, string[]>>({});
  const [membershipExtraIds, setMembershipExtraIds] = useState<string[]>([]);
  const [selectedSectorKey, setSelectedSectorKey] = useState("");
  const [sectorStockDraft, setSectorStockDraft] = useState("");
  const [firstPassJobId, setFirstPassJobId] = useState<string | null>(null);
  const [firstPassProgressText, setFirstPassProgressText] = useState("");
  const [firstPassEvidenceText, setFirstPassEvidenceText] = useState("");
  const [firstPassSnapshotId, setFirstPassSnapshotId] = useState<string | null>(null);
  const [firstPassDigest, setFirstPassDigest] = useState<string | null>(null);
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const [evidenceStatusFilter, setEvidenceStatusFilter] = useState("");
  const [evidenceSearch, setEvidenceSearch] = useState("");
  const [chipEvidenceOffset, setChipEvidenceOffset] = useState(0);
  const [chipEvidenceSearch, setChipEvidenceSearch] = useState("");
  const [chipEvidenceQuery, setChipEvidenceQuery] = useState("");
  const [candidateIdsText, setCandidateIdsText] = useState("");
  const [committedCandidateIdsText, setCommittedCandidateIdsText] = useState("");
  const [candidateSetText, setCandidateSetText] = useState("");
  const [candidateSetVersion, setCandidateSetVersion] = useState(0);
  const [candidateSetConflict, setCandidateSetConflict] = useState<string | null>(null);
  const [candidateConflictServerIds, setCandidateConflictServerIds] = useState<string[]>([]);
  const [firstPassJobs, setFirstPassJobs] = useState<Record<string, unknown>[]>([]);
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

  const candidateSetQuery = useQuery({
    queryKey: ["tw-research-candidate-set-latest"],
    queryFn: async () => {
      try {
        return await fetchTwResearchLatestCandidateSet();
      } catch (error) {
        if (toApiError(error).status === 404) return null;
        throw error;
      }
    },
    staleTime: 15_000,
    retry: false,
  });

  const researchRunQuery = useQuery({
    queryKey: ["tw-research-run-current"],
    queryFn: async (): Promise<TwResearchRun | null> => {
      try {
        return await fetchTwResearchRunCurrent();
      } catch (error) {
        const status = toApiError(error).status;
        if (status === 404) return null;
        throw error;
      }
    },
    staleTime: 5_000,
    refetchInterval: (query) => {
      const phase = query.state.data?.phase;
      if (phase === "phase1_first_pass" || phase === "phase2_technical") return 5_000;
      return false;
    },
    retry: false,
  });

  const [reportEvidenceText, setReportEvidenceText] = useState("");
  const [expandedLedgerIds, setExpandedLedgerIds] = useState<Record<string, boolean>>({});

  const [firstPassJobsStale, setFirstPassJobsStale] = useState(false);
  const [includeArchivedJobs, setIncludeArchivedJobs] = useState(false);
  const [jobManageBusy, setJobManageBusy] = useState(false);
  const [jobManageMessage, setJobManageMessage] = useState<string | null>(null);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleteModalMode, setDeleteModalMode] = useState<"job" | "full_run">("job");
  const [deletePreview, setDeletePreview] = useState<Record<string, unknown> | null>(
    null,
  );
  const PENDING_JOB_DELETION_KEY = "tw-research-pending-deletion-job";
  const PENDING_RUN_DELETION_KEY = "tw-research-pending-deletion-run";
  const readSession = (key: string): string | null => {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  };
  const writeSession = (key: string, value: string | null) => {
    try {
      if (value == null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch {
      /* ignore quota / private mode */
    }
  };
  const [pendingDeletionJobId, setPendingDeletionJobIdState] = useState<string | null>(
    () => (typeof window === "undefined" ? null : readSession(PENDING_JOB_DELETION_KEY)),
  );
  const [pendingFullRunDeletion, setPendingFullRunDeletionState] = useState(false);
  const setPendingDeletionJobId = (jobId: string | null) => {
    setPendingDeletionJobIdState(jobId);
    writeSession(PENDING_JOB_DELETION_KEY, jobId);
  };
  const setPendingFullRunDeletion = (pending: boolean) => {
    setPendingFullRunDeletionState(pending);
    writeSession(PENDING_RUN_DELETION_KEY, pending ? "1" : null);
  };
  const [confirmModal, setConfirmModal] = useState<"cancel" | "archive" | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (readSession(PENDING_RUN_DELETION_KEY) === "1") {
      setPendingFullRunDeletionState(true);
    }
  }, []);

  const firstPassJobsQuery = useQuery({
    queryKey: ["tw-research-first-pass-jobs", includeArchivedJobs],
    queryFn: async () => listTwResearchFirstPassJobs(20, includeArchivedJobs),
    staleTime: 15_000,
    // Avoid hammering an unavailable Postgres; manual retry is available.
    retry: false,
  });

  const invalidateJobQueries = () => {
    void queryClient.invalidateQueries({ queryKey: ["tw-research-first-pass-jobs"] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-run-current"] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-candidate-set"] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-scans"] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-scan"] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-progress"] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-manifest"] });
    void queryClient.invalidateQueries({ queryKey: ["tw-research-chip-evidence"] });
    if (firstPassJobId) {
      void queryClient.invalidateQueries({
        queryKey: ["tw-research-first-pass-progress", firstPassJobId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["tw-research-first-pass-symbols", firstPassJobId],
      });
    }
  };

  const blockerReasonText = (codes: string[]): string => {
    if (codes.length === 0) return t("research.tw.blocker.unknown");
    return codes
      .map((code) => {
        const key = `research.tw.blocker.${code}` as TranslationKey;
        const translated = t(key);
        return translated === key ? t("research.tw.blocker.unknown") : translated;
      })
      .join(" · ");
  };

  const closureCounts = (preview: Record<string, unknown> | null) => {
    const closure =
      preview && typeof preview.closure === "object" && preview.closure
        ? (preview.closure as Record<string, unknown>)
        : {};
    return {
      jobs: Number(closure.tw_research_first_pass_jobs ?? 0),
      symbols: Number(closure.tw_research_first_pass_symbol_results ?? 0),
      snapshots: Number(closure.tw_research_first_pass_snapshots ?? 0),
      candidates: Number(closure.tw_research_candidate_sets ?? 0),
      scans: Number(closure.tw_research_scans ?? 0),
      exports: Number(closure.tw_research_export_snapshots ?? 0),
    };
  };

  const openDeletePreview = async (mode: "job" | "full_run") => {
    if (jobManageBusy) return;
    if (mode === "job" && !firstPassJobId) return;
    setJobManageBusy(true);
    setJobManageMessage(null);
    try {
      if (mode === "full_run") {
        const preview = await fetchTwResearchRunDeletionPreview();
        setDeletePreview(preview);
        setDeleteModalMode("full_run");
      } else {
        const preview = await fetchTwResearchFirstPassDeletionPreview(
          firstPassJobId as string,
        );
        if (preview.requiresFullRunDelete) {
          const full = await fetchTwResearchRunDeletionPreview();
          setDeletePreview(full);
          setDeleteModalMode("full_run");
        } else {
          setDeletePreview(preview);
          setDeleteModalMode("job");
        }
      }
      setDeleteModalOpen(true);
    } catch (error) {
      const detail =
        error instanceof Error ? error.message : String(error ?? "error");
      setJobManageMessage(detail);
    } finally {
      setJobManageBusy(false);
    }
  };

  const confirmDeleteFromModal = async () => {
    if (!deletePreview || jobManageBusy) return;
    const token = String(deletePreview.previewToken ?? "");
    if (!token) {
      setJobManageMessage(t("research.tw.blocker.preview_stale"));
      return;
    }
    const blockerCodes = Array.isArray(deletePreview.blockers)
      ? deletePreview.blockers.map((b) =>
          typeof b === "object" && b && "code" in b
            ? String((b as { code: string }).code)
            : String(b),
        )
      : [];
    const hardBlock = blockerCodes.filter((code) => code !== "deletion_in_progress");
    if (deleteModalMode === "job" && hardBlock.length > 0) {
      if (hardBlock.includes("run_owned_requires_full_run_delete")) {
        setDeleteModalOpen(false);
        await openDeletePreview("full_run");
        return;
      }
      setJobManageMessage(
        t("research.tw.deleteJobBlocked", {
          reason: blockerReasonText(hardBlock),
        }),
      );
      setDeleteModalOpen(false);
      return;
    }
    if (deleteModalMode === "full_run" && hardBlock.length > 0) {
      setJobManageMessage(
        t("research.tw.deleteJobBlocked", {
          reason: blockerReasonText(hardBlock),
        }),
      );
      setDeleteModalOpen(false);
      return;
    }
    setJobManageBusy(true);
    try {
      const result =
        deleteModalMode === "full_run"
          ? await deleteTwResearchRunCurrent(token)
          : await deleteTwResearchFirstPassJob(firstPassJobId as string, token);
      setDeleteModalOpen(false);
      setDeletePreview(null);
      if (result.deletionState === "requested") {
        if (deleteModalMode === "full_run") {
          setPendingFullRunDeletion(true);
          setJobManageMessage(t("research.tw.deleteFullRunRequested"));
        } else {
          setJobManageMessage(t("research.tw.deleteJobRequested"));
          if (firstPassJobId) setPendingDeletionJobId(firstPassJobId);
        }
      } else if (result.deleted) {
        const counts = result.counts;
        const countsText =
          counts && typeof counts === "object"
            ? Object.entries(counts as Record<string, unknown>)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(", ")
            : "";
        setPendingDeletionJobId(null);
        setPendingFullRunDeletion(false);
        setFirstPassJobId(null);
        setFirstPassProgressText("");
        setFirstPassSnapshotId(null);
        setJobManageMessage(
          countsText
            ? t("research.tw.deleteJobGone", { counts: countsText })
            : null,
        );
      }
      invalidateJobQueries();
    } catch (error) {
      const api = toApiError(error);
      const detail = String(api.detail || api.message || "unknown");
      const codeMatch = detail.match(
        /run_owned_requires_full_run_delete|snapshot_shared_with_other_job|snapshot_referenced_by_candidate_set|snapshot_referenced_by_research_run|scan_provenance_references_snapshot|preview_stale|legacy_unowned|worker_quiescence_timeout|deletion_in_progress/,
      );
      setJobManageMessage(
        t("research.tw.deleteJobFailed", {
          reason: blockerReasonText([codeMatch?.[0] || "unknown"]),
        }),
      );
      setDeleteModalOpen(false);
    } finally {
      setJobManageBusy(false);
    }
  };

  const runJobManage = async (
    action: "cancel" | "archive" | "unarchive" | "delete" | "delete_full_run" | "retry_delete",
  ) => {
    if (jobManageBusy) return;
    if (action === "delete") {
      await openDeletePreview("job");
      return;
    }
    if (action === "delete_full_run") {
      await openDeletePreview("full_run");
      return;
    }
    if (action === "retry_delete") {
      if (pendingDeletionJobId) {
        setFirstPassJobId(pendingDeletionJobId);
        await openDeletePreview("job");
      } else {
        await openDeletePreview("full_run");
      }
      return;
    }
    if (!firstPassJobId) return;
    if (action === "cancel" || action === "archive") {
      setConfirmModal(action);
      return;
    }
    setJobManageBusy(true);
    setJobManageMessage(null);
    try {
      if (action === "unarchive") {
        await unarchiveTwResearchFirstPassJob(firstPassJobId);
      }
      invalidateJobQueries();
    } catch (error) {
      const detail =
        error instanceof Error ? error.message : String(error ?? "error");
      setJobManageMessage(detail);
    } finally {
      setJobManageBusy(false);
    }
  };

  const confirmManageModal = async () => {
    if (!confirmModal || !firstPassJobId || jobManageBusy) return;
    setJobManageBusy(true);
    setJobManageMessage(null);
    try {
      if (confirmModal === "cancel") {
        await cancelTwResearchFirstPassJob(firstPassJobId);
      } else if (confirmModal === "archive") {
        await archiveTwResearchFirstPassJob(firstPassJobId);
      }
      setConfirmModal(null);
      invalidateJobQueries();
    } catch (error) {
      const detail =
        error instanceof Error ? error.message : String(error ?? "error");
      setJobManageMessage(detail);
      setConfirmModal(null);
    } finally {
      setJobManageBusy(false);
    }
  };

  const formatTombstoneCounts = (counts: unknown): string => {
    if (!counts || typeof counts !== "object") return "";
    return Object.entries(counts as Record<string, unknown>)
      .map(([k, v]) => `${k}=${String(v)}`)
      .join(", ");
  };

  useEffect(() => {
    if (!pendingDeletionJobId) return;
    let cancelled = false;
    const deletedJobId = pendingDeletionJobId;
    const tick = async () => {
      try {
        const status = await fetchTwResearchFirstPassDeletionStatus(deletedJobId);
        if (cancelled) return;
        if (status.gone || status.outcome === "deleted") {
          setPendingDeletionJobId(null);
          const countsText = formatTombstoneCounts(status.counts);
          setJobManageMessage(
            countsText
              ? t("research.tw.deleteJobGone", { counts: countsText })
              : t("research.tw.deleteJobGone", { counts: "—" }),
          );
          if (firstPassJobId === deletedJobId) {
            setFirstPassJobId(null);
            setFirstPassProgressText("");
            setFirstPassSnapshotId(null);
          }
          void queryClient.removeQueries({
            queryKey: ["tw-research-first-pass-progress", deletedJobId],
          });
          void queryClient.removeQueries({
            queryKey: ["tw-research-first-pass-symbols", deletedJobId],
          });
          invalidateJobQueries();
          return;
        }
        if (status.deletionState === "failed" || status.deletionError) {
          setJobManageMessage(
            t("research.tw.deleteJobFailed", {
              reason: blockerReasonText([
                String(status.deletionError || "worker_quiescence_timeout"),
              ]),
            }),
          );
        } else {
          setJobManageMessage(t("research.tw.deleteJobRequested"));
        }
      } catch {
        // 404 from other users / missing — stop polling.
        if (!cancelled) setPendingDeletionJobId(null);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll only while pending id set
  }, [pendingDeletionJobId]);

  useEffect(() => {
    if (!pendingFullRunDeletion) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const status = await fetchTwResearchRunDeletionStatus();
        if (cancelled) return;
        if (status.gone || status.outcome === "deleted") {
          setPendingFullRunDeletion(false);
          setPendingDeletionJobId(null);
          setFirstPassJobId(null);
          setFirstPassProgressText("");
          setFirstPassSnapshotId(null);
          const countsText = formatTombstoneCounts(status.counts);
          setJobManageMessage(
            countsText
              ? t("research.tw.deleteJobGone", { counts: countsText })
              : t("research.tw.deleteJobGone", { counts: "—" }),
          );
          invalidateJobQueries();
          return;
        }
        if (status.deletionState === "failed" || status.deletionError) {
          setJobManageMessage(
            t("research.tw.deleteJobFailed", {
              reason: blockerReasonText([
                String(status.deletionError || "unknown"),
              ]),
            }),
          );
        } else {
          setJobManageMessage(t("research.tw.deleteFullRunRequested"));
        }
      } catch {
        if (!cancelled) setPendingFullRunDeletion(false);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll only while pending full-run
  }, [pendingFullRunDeletion]);

  useEffect(() => {
    if (firstPassJobsQuery.isError) {
      // Keep last verified jobs visible but mark them stale — never invent empty success.
      setFirstPassJobsStale(firstPassJobs.length > 0);
      return;
    }
    const jobs = firstPassJobsQuery.data;
    if (!jobs) return;
    setFirstPassJobs(jobs);
    setFirstPassJobsStale(false);
    if (firstPassJobId || jobs.length === 0) return;
    const latest = jobs[0];
    const jobId = String(latest.jobId ?? "");
    if (!jobId) return;
    setFirstPassJobId(jobId);
    setFirstPassProgressText(JSON.stringify(latest, null, 2));
    if (typeof latest.activeSnapshotId === "string" && latest.activeSnapshotId) {
      setFirstPassSnapshotId(latest.activeSnapshotId);
    }
  }, [
    firstPassJobsQuery.data,
    firstPassJobsQuery.isError,
    firstPassJobId,
    firstPassJobs.length,
  ]);

  useEffect(() => {
    const candidateSet = candidateSetQuery.data;
    if (!candidateSet) return;
    setCandidateSetVersion(Number(candidateSet.version ?? 0));
    setCandidateSetText(JSON.stringify(candidateSet, null, 2));
    if (Array.isArray(candidateSet.stockIds)) {
      const text = candidateSet.stockIds.map(String).join("\n");
      setCommittedCandidateIdsText(text);
      // Only seed the draft once so refresh/conflict never clobber unsaved edits.
      setCandidateIdsText((current) => (current.trim() ? current : text));
    }
    // Only seed snapshot/digest from the saved candidate set when no first-pass
    // job is active. Starting a new job clears these; do not re-apply the prior
    // snapshot while the new job is incomplete.
    if (!firstPassJobId) {
      if (
        typeof candidateSet.firstPassSnapshotId === "string" &&
        candidateSet.firstPassSnapshotId &&
        !firstPassSnapshotId
      ) {
        setFirstPassSnapshotId(candidateSet.firstPassSnapshotId);
      }
      if (
        typeof candidateSet.firstPassDigest === "string" &&
        candidateSet.firstPassDigest &&
        !firstPassDigest
      ) {
        setFirstPassDigest(candidateSet.firstPassDigest);
      }
    }
  }, [candidateSetQuery.data, firstPassDigest, firstPassJobId, firstPassSnapshotId]);

  const applyMembershipState = (membership: Record<string, unknown>) => {
    setMembershipText(JSON.stringify(membership, null, 2));
    setMembershipVersion(Number(membership.version ?? 0));
    setMembershipThemeKeys(
      Array.isArray(membership.themeKeys)
        ? membership.themeKeys.map(String)
        : [],
    );
    setDisabledThemeKeys(
      Array.isArray(membership.disabledThemeKeys)
        ? membership.disabledThemeKeys.map(String)
        : [],
    );
    setSectorAdditions(
      membership.sectorAdditions && typeof membership.sectorAdditions === "object"
        ? Object.fromEntries(
            Object.entries(membership.sectorAdditions as Record<string, unknown>).map(
              ([key, values]) => [
                key,
                Array.isArray(values) ? values.map(String) : [],
              ],
            ),
          )
        : {},
    );
    setSectorRemovals(
      membership.sectorRemovals && typeof membership.sectorRemovals === "object"
        ? Object.fromEntries(
            Object.entries(membership.sectorRemovals as Record<string, unknown>).map(
              ([key, values]) => [
                key,
                Array.isArray(values) ? values.map(String) : [],
              ],
            ),
          )
        : {},
    );
    setMembershipExtraIds(
      Array.isArray(membership.extraStockIds)
        ? membership.extraStockIds.map(String)
        : [],
    );
    const sectorKeys = Object.keys(
      (membership.sectorMembership as Record<string, unknown> | undefined) ?? {},
    );
    if (!selectedSectorKey && sectorKeys.length) {
      setSelectedSectorKey(sectorKeys[0]);
    }
  };

  useEffect(() => {
    if (!membershipQuery.data) return;
    applyMembershipState(membershipQuery.data);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync authoritative membership once loaded
  }, [membershipQuery.data]);

  const membershipEditPayload = useMemo(
    () => ({
      themeKeys: membershipThemeKeys,
      disabledThemeKeys,
      sectorAdditions,
      sectorRemovals,
      extraStockIds: membershipExtraIds,
    }),
    [
      disabledThemeKeys,
      membershipExtraIds,
      membershipThemeKeys,
      sectorAdditions,
      sectorRemovals,
    ],
  );

  const sectorMembership = useMemo(() => {
    const raw = membershipQuery.data?.sectorMembership;
    if (!raw || typeof raw !== "object") return {} as Record<string, string[]>;
    return Object.fromEntries(
      Object.entries(raw as Record<string, unknown>).map(([key, values]) => [
        key,
        Array.isArray(values) ? values.map(String) : [],
      ]),
    );
  }, [membershipQuery.data]);

  const selectedSectorMembers = sectorMembership[selectedSectorKey] ?? [];

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

  const pinnedFirstPassSnapshotId = useMemo(() => {
    const fromScan = scan?.candidateProvenance?.firstPassSnapshotId;
    if (typeof fromScan === "string" && fromScan) return fromScan;
    if (typeof candidateSetQuery.data?.firstPassSnapshotId === "string") {
      return candidateSetQuery.data.firstPassSnapshotId;
    }
    return firstPassSnapshotId;
  }, [candidateSetQuery.data?.firstPassSnapshotId, firstPassSnapshotId, scan]);

  const frozenFirstPassQuery = useQuery({
    queryKey: ["tw-research-first-pass-snapshot", pinnedFirstPassSnapshotId],
    enabled: Boolean(pinnedFirstPassSnapshotId),
    queryFn: () => fetchTwResearchFirstPassSnapshot(pinnedFirstPassSnapshotId as string),
    staleTime: 60_000,
    retry: false,
  });

  const chipEvidenceQueryResult = useQuery({
    queryKey: [
      "tw-research-chip-evidence",
      pinnedFirstPassSnapshotId,
      chipEvidenceOffset,
      chipEvidenceQuery,
    ],
    enabled: Boolean(pinnedFirstPassSnapshotId),
    queryFn: () =>
      fetchTwResearchFirstPassChipEvidence(pinnedFirstPassSnapshotId as string, {
        offset: chipEvidenceOffset,
        limit: PAGE_SIZE,
        q: chipEvidenceQuery || undefined,
      }),
    staleTime: 60_000,
    retry: false,
  });

  useEffect(() => {
    setChipEvidenceOffset(0);
  }, [pinnedFirstPassSnapshotId]);

  // Historical scans must use their pinned candidate-set version, not "latest".
  const pinnedCandidateSetId = scan?.candidateProvenance?.candidateSetId;
  const pinnedCandidateSetVersion = scan?.candidateProvenance?.candidateSetVersion;
  const pinnedCandidateSetQuery = useQuery({
    queryKey: [
      "tw-research-candidate-set",
      pinnedCandidateSetId,
      pinnedCandidateSetVersion,
    ],
    enabled:
      Boolean(pinnedCandidateSetId) &&
      pinnedCandidateSetVersion !== undefined &&
      pinnedCandidateSetVersion !== null,
    queryFn: () =>
      fetchTwResearchCandidateSet(
        String(pinnedCandidateSetId),
        Number(pinnedCandidateSetVersion),
      ),
    staleTime: 60_000,
    retry: false,
  });
  const watchlistCandidateSet =
    pinnedCandidateSetQuery.data ??
    (scan?.candidateProvenance?.approvedStockIds
      ? {
          stockIds: scan.candidateProvenance.approvedStockIds,
          selectionReasons: {},
        }
      : candidateSetQuery.data);
  const progress = progressQuery.data?.scanId === scanId ? progressQuery.data : null;
  const displayStatus = progress?.status ?? scan?.status ?? "";
  const displayProcessed = progress?.processedCount ?? scan?.processedCount ?? 0;
  const displayUniverse = progress?.universeSize ?? scan?.universeSize ?? 0;
  const displayRemaining = progress?.remainingCount ?? scan?.remainingCount ?? 0;
  const displayDayPass = progress?.dayPassCount ?? scan?.dayPassCount ?? 0;
  const displayMonthPass = progress?.monthPassCount ?? scan?.monthPassCount ?? 0;
  const displayDailyDeferred =
    progress?.dailyDeferredCount ?? scan?.dailyDeferredCount ?? 0;
  const displayStageOrder = progress?.stageOrder ?? scan?.stageOrder;
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
  const plainScanStatusText = t(
    `research.tw.scanStatus.${technicalScanPlainStatusKey(displayStatus, {
      workerActive: displayWorker === "active",
    })}` as TranslationKey,
  );
  const plainScanQualityText = displayQuality
    ? t(
        `research.tw.scanQuality.${technicalScanQualityPlainKey(displayQuality)}` as TranslationKey,
      )
    : "";

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
    mutationFn: async () => previewTwResearchMembership(membershipEditPayload),
    onSuccess: (membership) => {
      applyMembershipState(membership);
    },
  });

  const saveMembershipMutation = useMutation({
    mutationFn: async () =>
      saveTwResearchMembership({
        expectedVersion: membershipVersion,
        ...membershipEditPayload,
      }),
    onSuccess: (membership) => {
      applyMembershipState(membership);
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
      applyMembershipState(membership);
      void queryClient.invalidateQueries({ queryKey: ["tw-research-membership"] });
    },
  });

  const startFirstPassMutation = useMutation({
    mutationFn: async () => createTwResearchFirstPassJob({ useMembership: true }),
    onSuccess: (job) => {
      setFirstPassJobId(String(job.jobId ?? ""));
      setFirstPassProgressText(JSON.stringify(job, null, 2));
      // Clear prior job's snapshot/evidence so approvals cannot reuse stale data.
      setFirstPassSnapshotId(
        typeof job.activeSnapshotId === "string" && job.activeSnapshotId
          ? job.activeSnapshotId
          : null,
      );
      setFirstPassDigest(null);
      setFirstPassEvidenceText("");
      setEvidenceOffset(0);
      void queryClient.invalidateQueries({ queryKey: ["tw-research-first-pass-jobs"] });
    },
  });

  const selectedFirstPassJob = useMemo(() => {
    if (!firstPassJobId) return null;
    return (
      firstPassJobs.find((job) => String(job.jobId ?? "") === firstPassJobId) ?? null
    );
  }, [firstPassJobId, firstPassJobs]);

  const runOwnsSelectedFirstPassJob = useMemo(() => {
    const runJobId = researchRunQuery.data?.firstPassJobId;
    return Boolean(
      firstPassJobId &&
        runJobId &&
        String(runJobId) === String(firstPassJobId),
    );
  }, [firstPassJobId, researchRunQuery.data?.firstPassJobId]);

  const firstPassJobTerminalStatus = useMemo(() => {
    // Use research-run progress only when it describes the currently selected job.
    if (runOwnsSelectedFirstPassJob) {
      const runStatus = researchRunQuery.data?.firstPassProgress?.status;
      if (typeof runStatus === "string" && runStatus.trim()) {
        return runStatus;
      }
    }
    const progressStatus = firstPassProgressQuery.data?.status;
    if (typeof progressStatus === "string" && progressStatus.trim()) {
      return progressStatus;
    }
    const jobStatus = selectedFirstPassJob?.status;
    return typeof jobStatus === "string" ? jobStatus : "";
  }, [
    runOwnsSelectedFirstPassJob,
    researchRunQuery.data?.firstPassProgress?.status,
    firstPassProgressQuery.data?.status,
    selectedFirstPassJob?.status,
  ]);

  const firstPassJobIsTerminalFailed =
    firstPassJobTerminalStatus === "failed" ||
    firstPassJobTerminalStatus === "cancelled";

  const firstPassJobLastError = useMemo(() => {
    if (runOwnsSelectedFirstPassJob) {
      const err = researchRunQuery.data?.firstPassProgress?.lastError;
      if (typeof err === "string" && err.trim()) return err;
    }
    const fromJob = selectedFirstPassJob?.lastError;
    if (typeof fromJob === "string" && fromJob.trim()) return fromJob;
    const notes = selectedFirstPassJob?.notes;
    if (Array.isArray(notes)) {
      for (let i = notes.length - 1; i >= 0; i -= 1) {
        const note = String(notes[i] ?? "");
        if (note.startsWith("error:")) return note.slice("error:".length);
        if (note.startsWith("systemic_programming_error:")) return note;
      }
    }
    return "";
  }, [
    runOwnsSelectedFirstPassJob,
    researchRunQuery.data?.firstPassProgress?.lastError,
    selectedFirstPassJob,
  ]);

  const firstPassBatchMutation = useMutation({
    mutationFn: async (): Promise<
      Record<string, unknown> & { failedSymbols: Array<Record<string, unknown>> }
    > => {
      if (!firstPassJobId) throw new Error("missing_first_pass_job");
      const job = await runTwResearchFirstPassBatch(firstPassJobId, 10);
      // Per-batch failure detail: look up only this batch's failed symbols so the
      // banner never mixes in cumulative job failures.
      const counts = (job.batchCounts ?? {}) as { failed?: number };
      const batchIds = Array.isArray(job.batchIds) ? job.batchIds.map(String) : [];
      let failedSymbols: Array<Record<string, unknown>> = [];
      if (Number(counts.failed ?? 0) > 0 && batchIds.length > 0) {
        try {
          const stockIds = batchIds.slice(0, 50);
          const result = await fetchTwResearchFirstPassSymbols(firstPassJobId, {
            status: "failed",
            stockIds,
            limit: Math.min(Math.max(stockIds.length, 1), 50),
          });
          const wanted = new Set(stockIds);
          failedSymbols = (
            Array.isArray(result.records)
              ? (result.records as Array<Record<string, unknown>>)
              : []
          ).filter((row) => wanted.has(String(row.stockId ?? "")));
        } catch {
          failedSymbols = [];
        }
      }
      return { ...job, failedSymbols };
    },
    onSuccess: ({ failedSymbols: _failedSymbols, ...job }) => {
      setFirstPassProgressText(JSON.stringify(job, null, 2));
      const snapshotId =
        typeof job.activeSnapshotId === "string" && job.activeSnapshotId
          ? job.activeSnapshotId
          : null;
      if (snapshotId) {
        setFirstPassSnapshotId(snapshotId);
      }
      const status = String(job.status ?? "");
      if (
        firstPassJobId &&
        (status === "completed" || status === "partial" || status === "deferred")
      ) {
        // Server reconciles from durable job/scan rows; do not assert client status.
        void advanceTwResearchRunGate().finally(() => {
          void queryClient.invalidateQueries({ queryKey: ["tw-research-run-current"] });
        });
      }
      void queryClient.invalidateQueries({
        queryKey: ["tw-research-first-pass-progress", firstPassJobId],
      });
      void queryClient.invalidateQueries({ queryKey: ["tw-research-first-pass-jobs"] });
    },
  });

  const startFullResearchMutation = useMutation({
    mutationFn: async () =>
      startTwResearchRun({
        phase: "phase1_first_pass",
        tradingDay: taipeiTradingDay(),
      }),
    onSuccess: (run) => {
      if (run.firstPassJobId) {
        setFirstPassJobId(run.firstPassJobId);
        setFirstPassSnapshotId(null);
        setFirstPassDigest(null);
      }
      void queryClient.invalidateQueries({ queryKey: ["tw-research-run-current"] });
      void queryClient.invalidateQueries({ queryKey: ["tw-research-first-pass-jobs"] });
    },
  });

  const exportReportEvidenceMutation = useMutation({
    mutationFn: async () => {
      // Omit scanId unless its pinned first-pass matches the export target so a
      // lingering historical technical scan cannot force full-two-pass mismatch.
      const scanPinnedFp = scan?.candidateProvenance?.firstPassSnapshotId;
      const exportFp = firstPassSnapshotId ?? undefined;
      const includeScan =
        Boolean(scanId) &&
        (!exportFp || !scanPinnedFp || String(scanPinnedFp) === String(exportFp));
      return postTwResearchReportEvidence({
        tradingDay: taipeiTradingDay(),
        firstPassSnapshotId: exportFp,
        scanId: includeScan ? (scanId ?? undefined) : undefined,
      });
    },
    onSuccess: (evidence) => {
      setReportEvidenceText(JSON.stringify(evidence, null, 2));
    },
  });

  const loadEvidenceExampleMutation = useMutation({
    mutationFn: async () => fetchTwResearchReportEvidenceExampleZhTw(),
    onSuccess: (example) => {
      setReportEvidenceText(JSON.stringify(example, null, 2));
    },
  });

  const loadFirstPassEvidenceMutation = useMutation({
    mutationFn: async (offset: number) => {
      if (!firstPassJobId) throw new Error("missing_first_pass_job");
      return fetchTwResearchFirstPassSymbols(firstPassJobId, {
        offset,
        limit: PAGE_SIZE,
        status: evidenceStatusFilter || undefined,
        q: evidenceSearch.trim() || undefined,
      });
    },
    onSuccess: (result, offset) => {
      setEvidenceOffset(offset);
      const records = Array.isArray(result.records) ? result.records : [];
      const labeled = records.map((row) => {
        const evidence = (row as { evidence?: Record<string, unknown> }).evidence ?? {};
        const stockId = String((row as { stockId?: string }).stockId ?? "");
        const displayLabel =
          String((row as { displayLabel?: string }).displayLabel ?? "") ||
          formatTwStockLabel(
            stockId,
            String(evidence.stockName ?? (row as { stockName?: string }).stockName ?? ""),
            String(evidence.nameStatus ?? (row as { nameStatus?: string }).nameStatus ?? ""),
            t("research.tw.nameUnavailable"),
          );
        return { ...row, displayLabel };
      });
      setFirstPassEvidenceText(
        JSON.stringify(
          {
            ...result,
            records: labeled,
            serverSearch: evidenceSearch.trim() || null,
          },
          null,
          2,
        ),
      );
    },
  });

  const loadFirstPassManifestMutation = useMutation({
    mutationFn: async (chunkIndex: number) => {
      if (!firstPassSnapshotId) throw new Error("missing_first_pass_snapshot");
      const manifest = await fetchTwResearchFirstPassManifest(firstPassSnapshotId);
      const chunk = await fetchTwResearchFirstPassChunk(firstPassSnapshotId, chunkIndex);
      setFirstPassDigest(
        typeof manifest.contentDigest === "string" ? manifest.contentDigest : null,
      );
      return { manifest, chunk, chunkIndex };
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
      setCandidateSetConflict(null);
      return submitTwResearchCandidateSet({
        firstPassSnapshotId,
        firstPassDigest,
        stockIds: splitLines(candidateIdsText),
        origin: "manual",
        expectedVersion: candidateSetVersion,
      });
    },
    onSuccess: (candidateSet) => {
      const text = Array.isArray(candidateSet.stockIds)
        ? candidateSet.stockIds.map(String).join("\n")
        : candidateIdsText;
      setCandidateSetText(JSON.stringify(candidateSet, null, 2));
      setCandidateSetVersion(Number(candidateSet.version ?? 0));
      setCommittedCandidateIdsText(text);
      setCandidateIdsText(text);
      setCandidateConflictServerIds([]);
      setCandidateSetConflict(null);
      void queryClient.invalidateQueries({ queryKey: ["tw-research-candidate-set-latest"] });
    },
    onError: async (error) => {
      const apiError = toApiError(error);
      if (apiError.status !== 409) return;
      try {
        const latest = await fetchTwResearchLatestCandidateSet();
        const serverIds = Array.isArray(latest.stockIds)
          ? latest.stockIds.map(String)
          : [];
        // Preserve unsaved analyst draft; refresh only server version + committed view.
        setCandidateSetVersion(Number(latest.version ?? 0));
        setCandidateSetText(JSON.stringify(latest, null, 2));
        setCommittedCandidateIdsText(serverIds.join("\n"));
        setCandidateConflictServerIds(serverIds);
        setCandidateSetConflict(
          t("research.tw.candidateVersionConflict", {
            version: String(latest.version ?? 0),
          }),
        );
      } catch {
        setCandidateSetConflict(t("research.tw.candidateVersionConflictRefreshFailed"));
      }
    },
  });

  const startTechnicalFromCandidateMutation = useMutation({
    mutationFn: async () =>
      createTwResearchTechnicalScanFromCandidateSet({
        expectedVersion: candidateSetVersion || undefined,
        targetCount: 10,
      }),
    onSuccess: (created) => {
      setScanId(created.scanId);
      applyScanSnapshot(created);
      setMode("stock_ids");
      if (Array.isArray(created.stockIds)) {
        setStockIdsText(created.stockIds.map(String).join("\n"));
      }
      void queryClient.invalidateQueries({ queryKey: ["tw-research-scans"] });
      void queryClient.invalidateQueries({ queryKey: ["tw-research-run-current"] });
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
  const ledgerPageStockIds = useMemo(
    () =>
      pageRows
        .map((row) => String(row.stock_id ?? row.stockId ?? ""))
        .filter(Boolean)
        .slice(0, 50),
    [pageRows],
  );
  const frozenPageChipQuery = useQuery({
    queryKey: [
      "tw-research-chip-evidence-page",
      pinnedFirstPassSnapshotId,
      ledgerPageStockIds.join(","),
    ],
    enabled: Boolean(pinnedFirstPassSnapshotId) && ledgerPageStockIds.length > 0,
    queryFn: () =>
      fetchTwResearchFirstPassChipEvidence(pinnedFirstPassSnapshotId as string, {
        offset: 0,
        limit: 50,
        stockIds: ledgerPageStockIds,
      }),
    staleTime: 60_000,
    retry: false,
  });
  const frozenChipByStockId = useMemo(() => {
    const map = new Map<string, ChipEvidenceRow>();
    for (const row of frozenPageChipQuery.data?.rows ?? []) {
      const id = String(row.stockId ?? "");
      if (id) map.set(id, row as ChipEvidenceRow);
    }
    return map;
  }, [frozenPageChipQuery.data]);
  const watchlistReasonById = useMemo(() => {
    const reasonById = new Map<string, string>();
    const addReason = (id: string, reason: string) => {
      const prior = reasonById.get(id);
      reasonById.set(id, prior ? `${prior}+${reason}` : reason);
    };
    for (const id of scan?.dailyNotRunByDesignIds ?? []) {
      addReason(String(id), "daily_waiting_or_not_run");
    }
    for (const id of scan?.monthlyFailWatchlistIds ?? []) {
      addReason(String(id), "monthly_fail_watchlist");
    }
    for (const id of scan?.monthlyIncompleteIds ?? []) {
      addReason(String(id), "insufficient_evidence_monthly");
    }

    // Frozen first-pass universe + scan-pinned candidate set (not latest).
    const selected = new Set(
      ((watchlistCandidateSet?.stockIds as string[] | undefined) ?? []).map(String),
    );
    const frozenIds = Array.isArray(frozenFirstPassQuery.data?.stockIds)
      ? (frozenFirstPassQuery.data?.stockIds as string[]).map(String)
      : [];
    const reasons = (watchlistCandidateSet?.selectionReasons ?? {}) as Record<
      string,
      unknown
    >;
    const interestingIds = new Set<string>();
    const reasonInsufficient = new Set<string>();
    const watchList = reasons.fundamentalWatchIds ?? reasons.watchIds;
    if (Array.isArray(watchList)) {
      for (const id of watchList) interestingIds.add(String(id));
    }
    for (const [key, value] of Object.entries(reasons)) {
      if (key === "dailyOverrideIds" || key === "fundamentalWatchIds" || key === "watchIds") {
        continue;
      }
      const text = String(value ?? "").toLowerCase();
      if (
        text.includes("insufficient") ||
        text.includes("unavailable") ||
        text.includes("incomplete") ||
        text.includes("missing")
      ) {
        reasonInsufficient.add(key);
        continue;
      }
      if (text.includes("watch") || text.includes("promising") || text.includes("attractive")) {
        interestingIds.add(key);
      }
    }

    // Optional completeness hints from already-loaded first-pass evidence JSON.
    const insufficient = new Set<string>(reasonInsufficient);
    try {
      const parsed = firstPassEvidenceText ? JSON.parse(firstPassEvidenceText) : null;
      const records = Array.isArray(parsed?.records)
        ? parsed.records
        : Array.isArray(parsed?.chunk?.rows)
          ? parsed.chunk.rows
          : [];
      for (const row of records) {
        const id = String(row?.stockId ?? row?.stock_id ?? "");
        if (!id) continue;
        const completeness = (row?.completeness ?? row?.evidence?.completeness) as
          | { identityComplete?: boolean; fundamentalFieldsOk?: number; fieldsOk?: number }
          | undefined;
        if (
          completeness &&
          (completeness.identityComplete === false ||
            Number(completeness.fundamentalFieldsOk ?? completeness.fieldsOk ?? 1) <= 0)
        ) {
          insufficient.add(id);
        }
      }
    } catch {
      // Evidence text may be unrelated JSON; ignore parse failures.
    }

    for (const id of frozenIds) {
      if (selected.has(id)) continue;
      if (insufficient.has(id)) {
        addReason(id, "insufficient_evidence_fundamental");
      } else if (interestingIds.has(id)) {
        addReason(id, "fundamentally_interesting_not_selected");
      } else {
        // Neutral: not selected for MACD/KD — not an attractiveness claim.
        addReason(id, "first_pass_not_selected_for_technical");
      }
    }
    return reasonById;
  }, [
    firstPassEvidenceText,
    frozenFirstPassQuery.data?.stockIds,
    scan?.dailyNotRunByDesignIds,
    scan?.monthlyFailWatchlistIds,
    scan?.monthlyIncompleteIds,
    watchlistCandidateSet?.selectionReasons,
    watchlistCandidateSet?.stockIds,
  ]);
  const watchlistIds = useMemo(
    () =>
      Array.from(watchlistReasonById.keys()).sort((left, right) =>
        left.localeCompare(right),
      ),
    [watchlistReasonById],
  );
  const watchlistPageStockIds = useMemo(
    () =>
      watchlistIds
        .slice((watchlistPage - 1) * PAGE_SIZE, watchlistPage * PAGE_SIZE)
        .slice(0, 50),
    [watchlistIds, watchlistPage],
  );
  const frozenWatchlistChipQuery = useQuery({
    queryKey: [
      "tw-research-chip-evidence-watchlist-page",
      pinnedFirstPassSnapshotId,
      watchlistPageStockIds.join(","),
    ],
    enabled: Boolean(pinnedFirstPassSnapshotId) && watchlistPageStockIds.length > 0,
    queryFn: () =>
      fetchTwResearchFirstPassChipEvidence(pinnedFirstPassSnapshotId as string, {
        offset: 0,
        limit: 50,
        stockIds: watchlistPageStockIds,
      }),
    staleTime: 60_000,
    retry: false,
  });
  const frozenWatchlistChipByStockId = useMemo(() => {
    const map = new Map<string, ChipEvidenceRow>();
    for (const row of frozenWatchlistChipQuery.data?.rows ?? []) {
      const id = String(row.stockId ?? "");
      if (id) map.set(id, row as ChipEvidenceRow);
    }
    return map;
  }, [frozenWatchlistChipQuery.data]);
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
    {
      key: "first-pass-jobs",
      label: "research.tw.errorFirstPassJobs",
      error: firstPassJobsQuery.isFetching ? null : firstPassJobsQuery.error,
    },
    {
      key: "first-pass-batch",
      label: "research.tw.errorFirstPassBatch",
      error: firstPassBatchMutation.isPending ? null : firstPassBatchMutation.error,
    },
    {
      key: "research-run",
      label: "research.tw.errorResearchRun",
      error: researchRunQuery.isFetching ? null : researchRunQuery.error,
    },
    {
      key: "first-pass-progress",
      label: "research.tw.errorFirstPassProgress",
      error: firstPassProgressQuery.isFetching ? null : firstPassProgressQuery.error,
    },
  ];
  const plainLanguageErrorKeys = new Set([
    "first-pass-batch",
    "research-run",
    "first-pass-progress",
  ]);

  const chipEvidenceTotal = Number(chipEvidenceQueryResult.data?.total ?? 0);
  const chipEvidenceRangeText = t("research.tw.chipEvidenceMeta", {
    from: chipEvidenceTotal > 0 ? chipEvidenceOffset + 1 : 0,
    to: Math.min(chipEvidenceOffset + PAGE_SIZE, chipEvidenceTotal),
    total: chipEvidenceTotal,
  });

  const runOwnedFirstPassJobId =
    typeof researchRunQuery.data?.firstPassJobId === "string"
      ? researchRunQuery.data.firstPassJobId
      : null;

  const browsingHistoricalFirstPassJob = Boolean(
    firstPassJobId &&
      runOwnedFirstPassJobId &&
      String(firstPassJobId) !== String(runOwnedFirstPassJobId),
  );

  // Progress must match the selected job. Never mix run-owned nested progress
  // with a historical job the user is only browsing.
  const investorProgress = useMemo((): FirstPassProgressLike | null => {
    const jobProgress = firstPassProgressQuery.data as FirstPassProgressLike | undefined;
    if (browsingHistoricalFirstPassJob) {
      return jobProgress ?? null;
    }
    if (runOwnsSelectedFirstPassJob) {
      const nested = researchRunQuery.data?.firstPassProgress as
        | FirstPassProgressLike
        | null
        | undefined;
      if (
        jobProgress &&
        (jobProgress.universeSize != null || jobProgress.processedCount != null)
      ) {
        // Run-owned job: fill retry time / last error from the run's progress
        // when the job progress payload lacks them.
        return {
          ...jobProgress,
          nextRetryAt: jobProgress.nextRetryAt ?? nested?.nextRetryAt ?? null,
          lastError: jobProgress.lastError ?? nested?.lastError ?? null,
        };
      }
      if (nested) return nested;
      return jobProgress ?? null;
    }
    return jobProgress ?? null;
  }, [
    firstPassProgressQuery.data,
    browsingHistoricalFirstPassJob,
    runOwnsSelectedFirstPassJob,
    researchRunQuery.data?.firstPassProgress,
  ]);

  const firstPassDrain = researchRunQuery.data?.firstPassDrain ?? null;
  const scheduleEnabled = researchRunQuery.data?.scheduleEnabled ?? null;

  // A failed refetch with cached data means we only know the last state.
  const progressRefreshFailed =
    firstPassProgressQuery.isError && Boolean(firstPassProgressQuery.data);
  const runRefreshFailed = researchRunQuery.isError && Boolean(researchRunQuery.data);
  const investorStale = progressRefreshFailed || runRefreshFailed;
  const staleDataUpdatedAt = progressRefreshFailed
    ? firstPassProgressQuery.dataUpdatedAt
    : researchRunQuery.dataUpdatedAt;
  const investorStaleAsOf =
    investorStale && staleDataUpdatedAt
      ? new Date(staleDataUpdatedAt).toISOString()
      : null;

  const displayedJobCreatedAt =
    typeof selectedFirstPassJob?.createdAt === "string"
      ? selectedFirstPassJob.createdAt
      : null;
  const runOwnedJobCreatedAt = useMemo(() => {
    const fromList = firstPassJobs.find(
      (job) => String(job.jobId ?? "") === String(runOwnedFirstPassJobId ?? ""),
    )?.createdAt;
    if (typeof fromList === "string") return fromList;
    const fromRun = researchRunQuery.data?.createdAt;
    return typeof fromRun === "string" ? fromRun : null;
  }, [firstPassJobs, runOwnedFirstPassJobId, researchRunQuery.data?.createdAt]);

  // "This batch" banner comes only from the latest batch mutation result.
  const batchMutationData = firstPassBatchMutation.data;
  const batchResult = useMemo((): BatchResultView | null => {
    if (!batchMutationData) return null;
    if (String(batchMutationData.jobId ?? "") !== String(firstPassJobId ?? "")) {
      return null;
    }
    const counts = (batchMutationData.batchCounts ?? {}) as {
      ok?: number;
      partial?: number;
      failed?: number;
    };
    const ok = Number(counts.ok ?? 0);
    const partial = Number(counts.partial ?? 0);
    const failed = Number(counts.failed ?? 0);
    const processedInBatch = ok + partial + failed;
    const emptyFailures: BatchResultView["failures"] = [];
    if (batchMutationData.leaseHeld === true) {
      return {
        kind: "lease_held",
        processed: 0,
        ok: 0,
        partial: 0,
        failed: 0,
        failures: emptyFailures,
      };
    }
    const leaseLost = batchMutationData.leaseLost === true;
    const budgetStopped = batchMutationData.budgetStopped === true;
    if (leaseLost || (budgetStopped && processedInBatch === 0)) {
      return {
        kind: "batch_incomplete",
        processed: 0,
        ok: 0,
        partial: 0,
        failed: 0,
        failures: emptyFailures,
      };
    }
    if (processedInBatch <= 0) return null;
    const failures = (batchMutationData.failedSymbols ?? []).map((row) => {
      const stockId = String(row.stockId ?? "");
      const evidence = (row.evidence ?? {}) as Record<string, unknown>;
      return {
        stockId,
        label: formatTwStockLabel(
          stockId,
          String(evidence.stockName ?? row.stockName ?? ""),
          String(evidence.nameStatus ?? row.nameStatus ?? ""),
          t("research.tw.nameUnavailable"),
        ),
        reasonClass: normalizeFailureClass(evidence.failureClass, {
          retryExhausted: evidence.retryExhausted === true,
        }),
      };
    });
    return {
      kind: "processed",
      processed: processedInBatch,
      ok,
      partial,
      failed,
      failures,
    };
  }, [batchMutationData, firstPassJobId, t]);

  const firstPassBatchDisabledReason: TranslationKey | null = !firstPassJobId
    ? "research.tw.batchDisabledNeedJob"
    : firstPassJobIsTerminalFailed
      ? "research.tw.batchDisabledFailed"
      : firstPassBatchMutation.isPending
        ? "research.tw.batchDisabledBusy"
        : null;

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
            if (plainLanguageErrorKeys.has(failure.key)) {
              const copy = researchErrorCopy(failure.error, language);
              return (
                <div key={failure.key} data-testid={`tw-research-error-${failure.key}`}>
                  <RequestStateNotice
                    kind={errorToNoticeKind(apiError)}
                    scopeLabel={t(failure.label)}
                    status={apiError.status}
                    override={{ title: copy.title, nextAction: copy.nextAction }}
                  />
                  <AdvancedDetailsDisclosure>
                    <p
                      className="cds--helper-text-01"
                      data-testid={`tw-research-error-${failure.key}-raw`}
                    >
                      {researchErrorRawDetail(failure.error)}
                    </p>
                  </AdvancedDetailsDisclosure>
                </div>
              );
            }
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

          <Tile data-testid="tw-research-phase-banner">
            <Stack gap={3}>
              <h2 className="cds--productive-heading-03">{t("research.tw.phaseBannerTitle")}</h2>
              <p className="cds--body-compact-01" data-testid="tw-research-phase-label">
                {t(researchPhaseLabelKey(researchRunQuery.data?.phase))}
              </p>
              {researchRunQuery.data ? (
                <p className="cds--helper-text-01" data-testid="tw-research-phase-meta">
                  {t("research.tw.phaseMeta", {
                    status: String(researchRunQuery.data.status ?? "—"),
                  })}
                </p>
              ) : null}
              <ResearchInvestorSummary
                progress={investorProgress}
                displayedJobId={firstPassJobId}
                runOwnedJobId={runOwnedFirstPassJobId}
                browsingHistorical={browsingHistoricalFirstPassJob}
                drain={browsingHistoricalFirstPassJob ? null : firstPassDrain}
                scheduleEnabled={
                  browsingHistoricalFirstPassJob ? null : scheduleEnabled
                }
                displayedJobCreatedAt={displayedJobCreatedAt}
                runOwnedJobCreatedAt={runOwnedJobCreatedAt}
                batchResult={batchResult}
                stale={investorStale}
                staleAsOf={investorStaleAsOf}
              />
              <Stack gap={2} orientation="horizontal">
                <Button
                  kind="primary"
                  size="sm"
                  disabled={startFullResearchMutation.isPending}
                  onClick={() => startFullResearchMutation.mutate()}
                >
                  {t("research.tw.startFullResearch")}
                </Button>
                <Button
                  kind="tertiary"
                  size="sm"
                  disabled={exportReportEvidenceMutation.isPending}
                  onClick={() => exportReportEvidenceMutation.mutate()}
                >
                  {t("research.tw.exportReportEvidence")}
                </Button>
                <Button
                  kind="ghost"
                  size="sm"
                  disabled={loadEvidenceExampleMutation.isPending}
                  onClick={() => loadEvidenceExampleMutation.mutate()}
                >
                  {t("research.tw.loadEvidenceExample")}
                </Button>
              </Stack>
              <p className="cds--helper-text-01">{t("research.tw.evidencePackNote")}</p>
              {researchRunQuery.data || reportEvidenceText || browsingHistoricalFirstPassJob ? (
                <AdvancedDetailsDisclosure>
                  {researchRunQuery.data ? (
                    <p
                      className="cds--helper-text-01"
                      data-testid="tw-research-phase-meta-advanced"
                    >
                      {t("research.tw.phaseMetaAdvanced", {
                        runId: String(researchRunQuery.data.researchRunId ?? "—"),
                        jobId: String(
                          runOwnedFirstPassJobId ?? firstPassJobId ?? "—",
                        ),
                      })}
                    </p>
                  ) : null}
                  {browsingHistoricalFirstPassJob ? (
                    <p
                      className="cds--helper-text-01"
                      data-testid="tw-research-browsed-job-advanced"
                    >
                      {t("research.tw.browsedJobAdvanced", {
                        jobId: String(firstPassJobId ?? "—"),
                      })}
                    </p>
                  ) : null}
                  {reportEvidenceText ? (
                    <pre
                      className="cds--body-compact-01"
                      data-testid="tw-research-report-evidence"
                      style={{ whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}
                    >
                      {reportEvidenceText}
                    </pre>
                  ) : null}
                </AdvancedDetailsDisclosure>
              ) : null}
            </Stack>
          </Tile>

          <div data-testid="tw-research-tabs">
          <Tabs>
            <TabList aria-label={t("research.tw.tabsAria")} contained>
              <Tab data-testid="tw-research-tab-evidence">{t("research.tw.tabEvidence")}</Tab>
              <Tab data-testid="tw-research-tab-watchlist">{t("research.tw.tabWatchlist")}</Tab>
              <Tab data-testid="tw-research-tab-progress">{t("research.tw.tabProgress")}</Tab>
            </TabList>
            <TabPanels>
              <TabPanel>
                <Stack gap={6}>
          <Tile data-testid="tw-research-fundamentals">
            <Stack gap={4}>
              <div>
                <h2 className="cds--productive-heading-03">{t("research.tw.fundamentalsTitle")}</h2>
                <p className="cds--helper-text-01">{t("research.tw.fundamentalsNote")}</p>
                <p className="cds--helper-text-01">{t("research.tw.stageHint")}</p>
              </div>
              <p className="cds--helper-text-01" data-testid="tw-research-membership-meta">
                {t("research.tw.membershipMeta", {
                  version: membershipVersion,
                  updatedAt: String(membershipQuery.data?.updatedAt ?? "—"),
                  count: Array.isArray(membershipQuery.data?.resolvedStockIds)
                    ? membershipQuery.data.resolvedStockIds.length
                    : membershipExtraIds.length,
                })}
              </p>
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
              <div data-testid="tw-research-sector-editor">
                <h3 className="cds--productive-heading-02">{t("research.tw.sectorEditor")}</h3>
                <Stack gap={3}>
                  <Select
                    id="tw-research-sector-select"
                    labelText={t("research.tw.sectorSelect")}
                    value={selectedSectorKey}
                    onChange={(event) => setSelectedSectorKey(event.target.value)}
                  >
                    {Object.keys(sectorMembership).map((key) => (
                      <SelectItem
                        key={key}
                        value={key}
                        text={`${key}${disabledThemeKeys.includes(key) ? " (disabled)" : ""}`}
                      />
                    ))}
                  </Select>
                  {selectedSectorKey ? (
                    <Stack gap={2} orientation="horizontal">
                      <Button
                        kind="tertiary"
                        size="sm"
                        onClick={() => {
                          setDisabledThemeKeys((current) =>
                            current.includes(selectedSectorKey)
                              ? current.filter((key) => key !== selectedSectorKey)
                              : [...current, selectedSectorKey],
                          );
                        }}
                      >
                        {disabledThemeKeys.includes(selectedSectorKey)
                          ? t("research.tw.enableSector")
                          : t("research.tw.disableSector")}
                      </Button>
                    </Stack>
                  ) : null}
                  <TextInput
                    id="tw-research-sector-add-stock"
                    labelText={t("research.tw.sectorAddStock")}
                    value={sectorStockDraft}
                    onChange={(event) => setSectorStockDraft(event.target.value)}
                  />
                  <Stack gap={2} orientation="horizontal">
                    <Button
                      kind="secondary"
                      size="sm"
                      disabled={!selectedSectorKey || !sectorStockDraft.trim()}
                      onClick={() => {
                        const stockId = sectorStockDraft.trim();
                        setSectorAdditions((current) => {
                          const existing = current[selectedSectorKey] ?? [];
                          if (existing.includes(stockId)) return current;
                          return {
                            ...current,
                            [selectedSectorKey]: [...existing, stockId],
                          };
                        });
                        setSectorRemovals((current) => {
                          const existing = current[selectedSectorKey] ?? [];
                          return {
                            ...current,
                            [selectedSectorKey]: existing.filter((id) => id !== stockId),
                          };
                        });
                        setSectorStockDraft("");
                      }}
                    >
                      {t("research.tw.sectorAddStock")}
                    </Button>
                  </Stack>
                  <ul data-testid="tw-research-sector-members">
                    {selectedSectorMembers.map((stockId) => {
                      const ledgerRow = ledger.find(
                        (item) =>
                          String(item.stockId ?? item.stock_id ?? "") === stockId,
                      );
                      const name =
                        String(
                          ledgerRow?.stockName ??
                            ledgerRow?.stock_name ??
                            "",
                        ) || "";
                      const nameStatus = String(
                        ledgerRow?.nameStatus ?? ledgerRow?.name_status ?? "",
                      );
                      return (
                      <li key={stockId}>
                        <Stack gap={2} orientation="horizontal">
                          <span>
                            {formatTwStockLabel(
                              stockId,
                              name || null,
                              nameStatus || (name ? "ok" : "unavailable"),
                              t("research.tw.nameUnavailable"),
                            )}
                          </span>
                          <Button
                            kind="ghost"
                            size="sm"
                            onClick={() => {
                              setSectorRemovals((current) => {
                                const existing = current[selectedSectorKey] ?? [];
                                if (existing.includes(stockId)) return current;
                                return {
                                  ...current,
                                  [selectedSectorKey]: [...existing, stockId],
                                };
                              });
                              setSectorAdditions((current) => {
                                const existing = current[selectedSectorKey] ?? [];
                                return {
                                  ...current,
                                  [selectedSectorKey]: existing.filter((id) => id !== stockId),
                                };
                              });
                            }}
                          >
                            {t("research.tw.sectorRemoveStock")}
                          </Button>
                        </Stack>
                      </li>
                      );
                    })}
                  </ul>
                  <TextArea
                    id="tw-research-membership-extra-ids"
                    labelText={t("research.tw.extraMembershipIds")}
                    value={membershipExtraIds.join("\n")}
                    onChange={(event) =>
                      setMembershipExtraIds(splitLines(event.target.value))
                    }
                    rows={3}
                  />
                </Stack>
              </div>
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
                  kind="tertiary"
                  size="sm"
                  disabled={!firstPassJobId || loadFirstPassEvidenceMutation.isPending}
                  onClick={() => loadFirstPassEvidenceMutation.mutate(0)}
                >
                  {t("research.tw.loadFirstPassEvidence")}
                </Button>
              </Stack>
              <AdvancedDetailsDisclosure>
                <Stack gap={3} orientation="horizontal">
                  <Button
                    kind="ghost"
                    size="sm"
                    data-testid="tw-research-run-first-pass-batch"
                    title={
                      firstPassBatchDisabledReason
                        ? t(firstPassBatchDisabledReason)
                        : t("research.tw.runFirstPassBatch")
                    }
                    disabled={
                      !firstPassJobId ||
                      firstPassBatchMutation.isPending ||
                      firstPassJobIsTerminalFailed
                    }
                    onClick={() => firstPassBatchMutation.mutate()}
                  >
                    {t("research.tw.runFirstPassBatch")}
                  </Button>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={!firstPassSnapshotId || loadFirstPassManifestMutation.isPending}
                    onClick={() => loadFirstPassManifestMutation.mutate(0)}
                  >
                    {t("research.tw.loadFirstPassManifest")}
                  </Button>
                </Stack>
                {firstPassBatchDisabledReason ? (
                  <p
                    className="cds--helper-text-01"
                    data-testid="tw-research-batch-disabled-reason"
                  >
                    {t(firstPassBatchDisabledReason)}
                  </p>
                ) : null}
              </AdvancedDetailsDisclosure>
              {firstPassJobIsTerminalFailed ? (
                <div data-testid="tw-research-first-pass-job-failed">
                  <RequestStateNotice
                    kind="error"
                    scopeLabel={t("research.tw.firstPassJobFailed")}
                    detail={
                      firstPassJobLastError
                        ? t("research.tw.firstPassJobLastError", {
                            error: firstPassJobLastError,
                          })
                        : t("research.tw.firstPassJobFailed")
                    }
                  />
                </div>
              ) : null}
              {firstPassJobsQuery.isError ? (
                <div data-testid="tw-research-first-pass-jobs-unavailable">
                  <RequestStateNotice
                    kind={errorToNoticeKind(firstPassJobsQuery.error)}
                    scopeLabel={t("research.tw.firstPassJobHistory")}
                    status={toApiError(firstPassJobsQuery.error).status}
                    detail={describeResearchFailure(firstPassJobsQuery.error)}
                    onRetry={() => {
                      void firstPassJobsQuery.refetch();
                    }}
                  />
                  {firstPassJobsStale ? (
                    <p
                      className="cds--helper-text-01"
                      data-testid="tw-research-first-pass-jobs-stale"
                    >
                      {t("research.tw.firstPassJobsStale")}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {!firstPassJobsQuery.isError &&
              firstPassJobsQuery.isSuccess &&
              firstPassJobs.length === 0 ? (
                <p
                  className="cds--helper-text-01"
                  data-testid="tw-research-first-pass-jobs-empty"
                >
                  {t("research.tw.firstPassJobsEmpty")}
                </p>
              ) : null}
              <label className="cds--label" htmlFor="tw-research-show-archived">
                <input
                  id="tw-research-show-archived"
                  data-testid="tw-research-show-archived"
                  type="checkbox"
                  checked={includeArchivedJobs}
                  onChange={(event) => setIncludeArchivedJobs(event.target.checked)}
                />{" "}
                {t("research.tw.showArchivedJobs")}
              </label>
              {firstPassJobs.length > 0 ? (
                <Select
                  id="tw-research-first-pass-job-history"
                  data-testid="tw-research-first-pass-job-history"
                  labelText={
                    firstPassJobsStale
                      ? t("research.tw.firstPassJobHistoryStale")
                      : t("research.tw.firstPassJobHistory")
                  }
                  value={firstPassJobId ?? ""}
                  onChange={(event) => {
                    const next = event.target.value;
                    if (!next) return;
                    setFirstPassJobId(next);
                    const match = firstPassJobs.find((job) => String(job.jobId) === next);
                    if (match) {
                      setFirstPassProgressText(JSON.stringify(match, null, 2));
                      setFirstPassSnapshotId(
                        typeof match.activeSnapshotId === "string"
                          ? match.activeSnapshotId
                          : null,
                      );
                    } else {
                      void fetchTwResearchFirstPassJob(next).then((job) => {
                        setFirstPassProgressText(JSON.stringify(job, null, 2));
                        if (typeof job.activeSnapshotId === "string") {
                          setFirstPassSnapshotId(job.activeSnapshotId);
                        }
                      });
                    }
                  }}
                >
                  {firstPassJobs.map((job) => (
                    <SelectItem
                      key={String(job.jobId)}
                      value={String(job.jobId)}
                      text={jobHistoryOptionLabel(
                        job as FirstPassProgressLike & {
                          createdAt?: string | null;
                          qualityStatus?: string | null;
                        },
                        t,
                        language,
                      )}
                    />
                  ))}
                </Select>
              ) : null}
              {firstPassJobId ? (
                <Stack gap={3} orientation="horizontal">
                  <Button
                    size="sm"
                    kind="ghost"
                    disabled={jobManageBusy}
                    data-testid="tw-research-job-cancel"
                    onClick={() => void runJobManage("cancel")}
                  >
                    {t("research.tw.cancelJob")}
                  </Button>
                  {selectedFirstPassJob?.archivedAt ? (
                    <Button
                      size="sm"
                      kind="ghost"
                      disabled={jobManageBusy}
                      data-testid="tw-research-job-unarchive"
                      onClick={() => void runJobManage("unarchive")}
                    >
                      {t("research.tw.unarchiveJob")}
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      kind="ghost"
                      disabled={jobManageBusy}
                      data-testid="tw-research-job-archive"
                      onClick={() => void runJobManage("archive")}
                    >
                      {t("research.tw.archiveJob")}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    kind="danger--ghost"
                    disabled={jobManageBusy}
                    data-testid="tw-research-job-delete"
                    onClick={() => void runJobManage("delete")}
                  >
                    {t("research.tw.deleteJob")}
                  </Button>
                  <Button
                    size="sm"
                    kind="danger--ghost"
                    disabled={jobManageBusy}
                    data-testid="tw-research-delete-full-run"
                    onClick={() => void runJobManage("delete_full_run")}
                  >
                    {t("research.tw.deleteFullResearch")}
                  </Button>
                </Stack>
              ) : null}
              {jobManageMessage ? (
                <Stack gap={2} orientation="horizontal">
                  <p
                    className="cds--helper-text-01"
                    data-testid="tw-research-job-manage-message"
                  >
                    {jobManageMessage}
                  </p>
                  {String(jobManageMessage).includes("失敗") ||
                  String(jobManageMessage).toLowerCase().includes("failed") ||
                  String(jobManageMessage).toLowerCase().includes("retry") ? (
                    <Button
                      size="sm"
                      kind="ghost"
                      disabled={jobManageBusy}
                      data-testid="tw-research-retry-delete"
                      onClick={() => void runJobManage("retry_delete")}
                    >
                      {t("research.tw.retryDelete")}
                    </Button>
                  ) : null}
                </Stack>
              ) : null}
              <ComposedModal
                open={confirmModal != null}
                onClose={() => setConfirmModal(null)}
                size="sm"
                data-testid="tw-research-confirm-modal"
              >
                <ModalHeader
                  title={t(
                    confirmModal === "archive"
                      ? "research.tw.archiveJobModalTitle"
                      : "research.tw.cancelJobModalTitle",
                  )}
                />
                <ModalBody>
                  <p className="cds--body-compact-01">
                    {t(
                      confirmModal === "archive"
                        ? "research.tw.archiveJobConfirm"
                        : "research.tw.cancelJobConfirm",
                    )}
                  </p>
                </ModalBody>
                <ModalFooter>
                  <Button kind="secondary" onClick={() => setConfirmModal(null)}>
                    {t("research.tw.confirmModalCancel")}
                  </Button>
                  <Button
                    kind={confirmModal === "archive" ? "primary" : "danger"}
                    disabled={jobManageBusy}
                    data-testid="tw-research-confirm-modal-confirm"
                    onClick={() => void confirmManageModal()}
                  >
                    {t("research.tw.confirmModalConfirm")}
                  </Button>
                </ModalFooter>
              </ComposedModal>
              <ComposedModal
                open={deleteModalOpen}
                onClose={() => {
                  setDeleteModalOpen(false);
                  setDeletePreview(null);
                }}
                size="md"
                data-testid="tw-research-delete-modal"
              >
                <ModalHeader
                  title={t(
                    deleteModalMode === "full_run"
                      ? "research.tw.deleteModalFullRunTitle"
                      : "research.tw.deleteModalTitle",
                  )}
                />
                <ModalBody>
                  <p className="cds--body-compact-01">{t("research.tw.deleteModalIntro")}</p>
                  {deletePreview ? (
                    <>
                      <p
                        className="cds--helper-text-01"
                        data-testid="tw-research-delete-closure"
                      >
                        {t("research.tw.deleteModalClosure", closureCounts(deletePreview))}
                      </p>
                      {Array.isArray(deletePreview.retained) &&
                      deletePreview.retained.length > 0 ? (
                        <p className="cds--helper-text-01">
                          {t("research.tw.deleteModalRetained", {
                            items: deletePreview.retained
                              .map((item) => {
                                if (typeof item === "object" && item && "code" in item) {
                                  const code = String((item as { code: string }).code);
                                  if (code === "first_pass_snapshot") {
                                    return language === "en"
                                      ? "shared first-round data"
                                      : "共用的第一輪資料";
                                  }
                                  return code;
                                }
                                return String(item);
                              })
                              .join(", "),
                          })}
                        </p>
                      ) : null}
                    </>
                  ) : null}
                </ModalBody>
                <ModalFooter>
                  <Button
                    kind="secondary"
                    onClick={() => {
                      setDeleteModalOpen(false);
                      setDeletePreview(null);
                    }}
                  >
                    {t("research.tw.deleteModalCancel")}
                  </Button>
                  <Button
                    kind="danger"
                    disabled={jobManageBusy}
                    data-testid="tw-research-delete-modal-confirm"
                    onClick={() => void confirmDeleteFromModal()}
                  >
                    {t("research.tw.deleteModalConfirm")}
                  </Button>
                </ModalFooter>
              </ComposedModal>
              <Stack gap={3} orientation="horizontal">
                <TextInput
                  id="tw-research-evidence-search"
                  labelText={t("research.tw.evidenceSearch")}
                  value={evidenceSearch}
                  onChange={(event) => setEvidenceSearch(event.target.value)}
                />
                <Select
                  id="tw-research-evidence-status"
                  labelText={t("research.tw.evidenceStatus")}
                  value={evidenceStatusFilter}
                  onChange={(event) => setEvidenceStatusFilter(event.target.value)}
                >
                  <SelectItem value="" text={t("research.tw.evidenceStatusAll")} />
                  <SelectItem value="ok" text={t("research.tw.evidenceStatusOk")} />
                  <SelectItem value="partial" text={t("research.tw.evidenceStatusPartial")} />
                  <SelectItem value="failed" text={t("research.tw.evidenceStatusFailed")} />
                  <SelectItem value="deferred" text={t("research.tw.evidenceStatusDeferred")} />
                </Select>
                <Button
                  kind="ghost"
                  size="sm"
                  disabled={!firstPassJobId || loadFirstPassEvidenceMutation.isPending}
                  onClick={() => loadFirstPassEvidenceMutation.mutate(0)}
                >
                  {t("research.tw.evidenceSearchApply")}
                </Button>
                <Button
                  kind="ghost"
                  size="sm"
                  disabled={!firstPassJobId || evidenceOffset <= 0 || loadFirstPassEvidenceMutation.isPending}
                  onClick={() =>
                    loadFirstPassEvidenceMutation.mutate(Math.max(0, evidenceOffset - PAGE_SIZE))
                  }
                >
                  {t("research.tw.evidencePrev")}
                </Button>
                <Button
                  kind="ghost"
                  size="sm"
                  disabled={!firstPassJobId || loadFirstPassEvidenceMutation.isPending}
                  onClick={() =>
                    loadFirstPassEvidenceMutation.mutate(evidenceOffset + PAGE_SIZE)
                  }
                >
                  {t("research.tw.evidenceNext")}
                </Button>
              </Stack>
              <AdvancedDetailsDisclosure>
                <p className="cds--helper-text-01">{t("research.tw.historicalJobsLabel")}</p>
                {selectedFirstPassJob ? (
                  <p
                    className="cds--helper-text-01"
                    data-testid="tw-research-job-history-raw-status"
                  >
                    {t("research.tw.jobHistoryRawStatus", {
                      status: String(selectedFirstPassJob.status ?? ""),
                      qualityStatus: String(selectedFirstPassJob.qualityStatus ?? ""),
                    })}
                  </p>
                ) : null}
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
              </AdvancedDetailsDisclosure>
              <Tile data-testid="tw-research-chip-evidence">
                <h3 className="cds--productive-heading-02">{t("research.tw.chipEvidenceTitle")}</h3>
                <p className="cds--helper-text-01">{t("research.tw.chipEvidenceDescription")}</p>
                <Stack gap={3} orientation="horizontal">
                  <TextInput
                    id="tw-research-chip-search"
                    labelText={t("research.tw.chipEvidenceSearch")}
                    value={chipEvidenceSearch}
                    onChange={(event) => setChipEvidenceSearch(event.target.value)}
                  />
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={!pinnedFirstPassSnapshotId}
                    onClick={() => {
                      setChipEvidenceOffset(0);
                      setChipEvidenceQuery(chipEvidenceSearch.trim());
                    }}
                  >
                    {t("research.tw.chipEvidenceSearchApply")}
                  </Button>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={!pinnedFirstPassSnapshotId || chipEvidenceOffset <= 0}
                    onClick={() =>
                      setChipEvidenceOffset((current) => Math.max(0, current - PAGE_SIZE))
                    }
                  >
                    {t("research.tw.evidencePrev")}
                  </Button>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={
                      !pinnedFirstPassSnapshotId ||
                      !chipEvidenceQueryResult.data ||
                      chipEvidenceOffset + PAGE_SIZE >=
                        Number(chipEvidenceQueryResult.data.total ?? 0)
                    }
                    onClick={() => setChipEvidenceOffset((current) => current + PAGE_SIZE)}
                  >
                    {t("research.tw.evidenceNext")}
                  </Button>
                </Stack>
                {!pinnedFirstPassSnapshotId ? (
                  <p className="cds--helper-text-01">{t("research.tw.chipEvidenceNeedSnapshot")}</p>
                ) : null}
                {chipEvidenceQueryResult.isLoading ? (
                  <InlineLoading description={t("research.tw.chipEvidenceLoading")} status="active" />
                ) : null}
                {chipEvidenceQueryResult.isError && !chipEvidenceQueryResult.data ? (
                  <RequestStateNotice
                    kind={errorToNoticeKind(chipEvidenceQueryResult.error)}
                    scopeLabel={t("research.tw.chipEvidenceTitle")}
                    status={toApiError(chipEvidenceQueryResult.error).status}
                    detail={describeResearchFailure(chipEvidenceQueryResult.error)}
                    onRetry={() => {
                      void chipEvidenceQueryResult.refetch();
                    }}
                  />
                ) : null}
                {chipEvidenceQueryResult.isError && chipEvidenceQueryResult.data ? (
                  <p className="cds--helper-text-01" data-testid="tw-research-chip-evidence-stale-banner">
                    {t("research.tw.chipEvidenceStaleBanner", {
                      capturedAt: String(
                        (chipEvidenceQueryResult.data.rows?.[0] as { institutional?: { capturedAt?: string } } | undefined)
                          ?.institutional?.capturedAt ?? "—",
                      ),
                    })}
                  </p>
                ) : null}
                {chipEvidenceQueryResult.data ? (
                  <p className="cds--helper-text-01" data-testid="tw-research-chip-evidence-meta">
                    {chipEvidenceRangeText}
                  </p>
                ) : null}
                {chipEvidenceQueryResult.data ? (
                  <AdvancedDetailsDisclosure>
                    <p
                      className="cds--helper-text-01"
                      data-testid="tw-research-chip-evidence-meta-advanced"
                    >
                      {t("research.tw.chipEvidenceMetaAdvanced", {
                        limit: PAGE_SIZE,
                        snapshot: pinnedFirstPassSnapshotId ?? "—",
                      })}
                    </p>
                  </AdvancedDetailsDisclosure>
                ) : null}
                <Stack gap={4}>
                  {(chipEvidenceQueryResult.data?.rows ?? []).map((row) => {
                    const stockId = String(row.stockId ?? "");
                    return (
                      <div key={stockId || JSON.stringify(row)} data-testid={`tw-research-chip-row-${stockId}`}>
                        <p className="cds--productive-heading-01">
                          {formatTwStockLabel(
                            stockId,
                            row.stockName as string | undefined,
                            row.nameStatus as string | undefined,
                            t("research.tw.nameUnavailable"),
                          )}
                        </p>
                        <ChipEvidenceCard
                          row={row as ChipEvidenceRow}
                          staleCacheBanner={
                            chipEvidenceQueryResult.isError && chipEvidenceQueryResult.data
                              ? {
                                  capturedAt:
                                    (row.institutional as { capturedAt?: string } | undefined)
                                      ?.capturedAt ?? null,
                                }
                              : null
                          }
                        />
                      </div>
                    );
                  })}
                </Stack>
              </Tile>
              <TextArea
                id="tw-research-candidate-ids"
                data-testid="tw-research-candidate-ids"
                labelText={t("research.tw.candidateIds")}
                value={candidateIdsText}
                onChange={(event) => setCandidateIdsText(event.target.value)}
                rows={3}
              />
              <p className="cds--helper-text-01" data-testid="tw-research-candidate-version">
                {t("research.tw.candidateVersion", { version: candidateSetVersion })}
              </p>
              {candidateSetConflict ? (
                <Stack gap={3}>
                  <RequestStateNotice
                    kind="validation"
                    scopeLabel={t("research.tw.saveCandidateSet")}
                    status={409}
                    detail={candidateSetConflict}
                  />
                  <TextArea
                    id="tw-research-candidate-committed"
                    data-testid="tw-research-candidate-committed"
                    labelText={t("research.tw.candidateCommitted")}
                    value={committedCandidateIdsText}
                    readOnly
                    rows={3}
                  />
                  <TextArea
                    id="tw-research-candidate-proposed"
                    data-testid="tw-research-candidate-proposed"
                    labelText={t("research.tw.candidateProposed")}
                    value={candidateIdsText}
                    readOnly
                    rows={3}
                  />
                  <Stack gap={3} orientation="horizontal">
                    <Button
                      kind="secondary"
                      size="sm"
                      data-testid="tw-research-candidate-reapply"
                      onClick={() => {
                        // Keep draft; only bump expected version already refreshed.
                        setCandidateSetConflict(null);
                      }}
                    >
                      {t("research.tw.candidateReapply")}
                    </Button>
                    <Button
                      kind="danger--tertiary"
                      size="sm"
                      data-testid="tw-research-candidate-discard"
                      onClick={() => {
                        setCandidateIdsText(committedCandidateIdsText);
                        setCandidateConflictServerIds([]);
                        setCandidateSetConflict(null);
                      }}
                    >
                      {t("research.tw.candidateDiscard")}
                    </Button>
                    <Button
                      kind="primary"
                      size="sm"
                      data-testid="tw-research-candidate-resolve-save"
                      disabled={saveCandidateSetMutation.isPending}
                      onClick={() => saveCandidateSetMutation.mutate()}
                    >
                      {t("research.tw.candidateResolveSave")}
                    </Button>
                  </Stack>
                  {candidateConflictServerIds.length ? (
                    <p className="cds--helper-text-01" data-testid="tw-research-candidate-server-count">
                      {t("research.tw.candidateServerCount", {
                        count: String(candidateConflictServerIds.length),
                      })}
                    </p>
                  ) : null}
                </Stack>
              ) : null}
              <Stack gap={3} orientation="horizontal">
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
                <Button
                  kind="secondary"
                  size="sm"
                  data-testid="tw-research-start-technical-from-candidates"
                  disabled={
                    candidateSetVersion <= 0 ||
                    startTechnicalFromCandidateMutation.isPending
                  }
                  onClick={() => startTechnicalFromCandidateMutation.mutate()}
                >
                  {t("research.tw.startTechnicalFromCandidates")}
                </Button>
              </Stack>
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

                </Stack>
              </TabPanel>
              <TabPanel>

          <Tile data-testid="tw-research-watchlist">
            <Stack gap={3}>
              <div>
                <h2 className="cds--productive-heading-03">{t("research.tw.tabWatchlist")}</h2>
                <p className="cds--helper-text-01">{t("research.tw.watchlistNote")}</p>
              </div>
              <p className="cds--body-compact-01" data-testid="tw-research-watchlist-counts">
                {t("research.tw.watchlistCounts", {
                  monthlyFail: Number(scan?.monthlyFailWatchlistCount ?? 0),
                  notRun: Number(scan?.dailyNotRunByDesignCount ?? 0),
                  monthlyIncomplete: Number(scan?.monthlyIncompleteCount ?? 0),
                  notSelectedTechnical: watchlistIds.filter((id) =>
                    String(watchlistReasonById.get(id) ?? "").includes(
                      "first_pass_not_selected_for_technical",
                    ),
                  ).length,
                  fundamentalWatch: watchlistIds.filter((id) =>
                    String(watchlistReasonById.get(id) ?? "").includes(
                      "fundamentally_interesting_not_selected",
                    ),
                  ).length,
                  insufficientFundamental: watchlistIds.filter((id) =>
                    String(watchlistReasonById.get(id) ?? "").includes(
                      "insufficient_evidence_fundamental",
                    ),
                  ).length,
                })}
              </p>
              <p className="cds--helper-text-01">{t("research.tw.expandHint")}</p>
              <TableContainer title={t("research.tw.tabWatchlist")}>
                <Table size="sm">
                  <TableHead>
                    <TableRow>
                      <TableHeader>{t("research.tw.expandDetails")}</TableHeader>
                      <TableHeader>{t("research.tw.colStock")}</TableHeader>
                      <TableHeader>{t("research.tw.colMonthly")}</TableHeader>
                      <TableHeader>{t("research.tw.colDaily")}</TableHeader>
                      <TableHeader>{t("research.tw.colTechnicalStage")}</TableHeader>
                      <TableHeader>{t("research.tw.colReason")}</TableHeader>
                      <TableHeader>{t("research.tw.colNote")}</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {watchlistIds
                      .slice(
                        (watchlistPage - 1) * PAGE_SIZE,
                        watchlistPage * PAGE_SIZE,
                      )
                      .map((id) => {
                        const row = ledger.find(
                          (item) =>
                            String(item.stockId ?? item.stock_id ?? "") === id,
                        );
                        const monthly = row?.monthly as { passStatus?: string } | undefined;
                        const daily = row?.daily as {
                          executionStatus?: string;
                          passStatus?: string;
                        } | undefined;
                        const expanded = Boolean(expandedLedgerIds[`wl:${id}`]);
                        const stageValue =
                          row?.technical_stage ?? row?.technicalStage;
                        const dailyValue =
                          daily?.executionStatus ?? daily?.passStatus;
                        return (
                          <Fragment key={id}>
                          <TableRow data-testid={`tw-research-watchlist-row-${id}`}>
                            <TableCell>
                              <Button
                                kind="ghost"
                                size="sm"
                                data-testid={`tw-research-watchlist-expand-${id}`}
                                onClick={() =>
                                  setExpandedLedgerIds((current) => ({
                                    ...current,
                                    [`wl:${id}`]: !current[`wl:${id}`],
                                  }))
                                }
                              >
                                {expanded ? "−" : "+"}
                              </Button>
                            </TableCell>
                            <TableCell>
                              {formatTwStockLabel(
                                id,
                                (row?.stockName ?? row?.stock_name) as string | undefined,
                                (row?.nameStatus ?? row?.name_status) as string | undefined,
                                t("research.tw.nameUnavailable"),
                              )}
                            </TableCell>
                            <TableCell>{String(monthly?.passStatus ?? "—")}</TableCell>
                            <TableCell>{String(dailyValue ?? "—")}</TableCell>
                            <TableCell>{String(stageValue ?? "—")}</TableCell>
                            <TableCell>{watchlistReasonById.get(id) ?? "—"}</TableCell>
                            <TableCell>{t("research.tw.watchlistRowNote")}</TableCell>
                          </TableRow>
                          {expanded ? (
                            <TableRow>
                              <TableCell colSpan={7}>
                                <div data-testid={`tw-research-watchlist-detail-${id}`}>
                                  {!pinnedFirstPassSnapshotId ? (
                                    <p className="cds--helper-text-01">
                                      {t("research.tw.chipEvidenceNeedSnapshot")}
                                    </p>
                                  ) : frozenWatchlistChipQuery.isPending ? (
                                    <InlineLoading
                                      description={t("research.tw.chipEvidenceLoading")}
                                      status="active"
                                    />
                                  ) : frozenWatchlistChipQuery.isError &&
                                    !frozenWatchlistChipQuery.data ? (
                                    <RequestStateNotice
                                      kind={errorToNoticeKind(frozenWatchlistChipQuery.error)}
                                      scopeLabel={t("research.tw.chipEvidenceTitle")}
                                      status={toApiError(frozenWatchlistChipQuery.error).status}
                                      detail={describeResearchFailure(
                                        frozenWatchlistChipQuery.error,
                                      )}
                                      onRetry={() => {
                                        void frozenWatchlistChipQuery.refetch();
                                      }}
                                    />
                                  ) : (
                                    <ChipEvidenceCard
                                      row={
                                        frozenWatchlistChipByStockId.get(id) ?? {
                                          stockId: id,
                                          status: "not_fetched",
                                          reason: "stock_not_in_first_pass_snapshot",
                                          notBuySignal: true,
                                        }
                                      }
                                      staleCacheBanner={
                                        frozenWatchlistChipQuery.isError &&
                                        frozenWatchlistChipQuery.data
                                          ? {
                                              capturedAt:
                                                frozenWatchlistChipByStockId.get(id)?.institutional
                                                  ?.capturedAt ?? null,
                                            }
                                          : null
                                      }
                                    />
                                  )}
                                  <pre
                                    className="cds--body-compact-01"
                                    style={{ whiteSpace: "pre-wrap", margin: 0 }}
                                  >
                                    {JSON.stringify(
                                      {
                                        reason: watchlistReasonById.get(id) ?? null,
                                        coverage: row?.coverage ?? null,
                                        monthly: row?.monthly ?? null,
                                        daily: row?.daily ?? null,
                                        note: t("research.tw.watchlistRowNote"),
                                      },
                                      null,
                                      2,
                                    )}
                                  </pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          ) : null}
                          </Fragment>
                        );
                      })}
                  </TableBody>
                </Table>
              </TableContainer>
              <Pagination
                data-testid="tw-research-watchlist-pagination"
                page={watchlistPage}
                pageSize={PAGE_SIZE}
                pageSizes={[PAGE_SIZE]}
                totalItems={watchlistIds.length}
                onChange={({ page: nextPage }) => setWatchlistPage(nextPage)}
              />
            </Stack>
          </Tile>
              </TabPanel>
              <TabPanel>
                <Stack gap={6}>
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
                <p className="cds--body-compact-01" data-testid="tw-research-status-line">
                  {displayStageOrder === "monthly_first"
                    ? t("research.tw.statusLineMonthlyFirst", {
                        status: plainScanStatusText,
                        processed: displayProcessed,
                        universe: displayUniverse,
                        remaining: displayRemaining,
                        monthPass: displayMonthPass,
                        dayPass: displayDayPass,
                        qualified: displayQualified,
                        deferred: displayDailyDeferred,
                        target: displayTarget,
                      })
                    : t("research.tw.statusLineDailyFirst", {
                        status: plainScanStatusText,
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
                {scan.candidateProvenance?.candidateSetId ? (
                  <div data-testid="tw-research-candidate-provenance">
                    <p className="cds--helper-text-01">
                      {t("research.tw.candidateProvenanceLine", {
                        count: String(
                          scan.candidateProvenance.approvedStockIds?.length ??
                            scan.stockIds?.length ??
                            0,
                        ),
                      })}
                    </p>
                    <AdvancedDetailsDisclosure>
                      <p
                        className="cds--helper-text-01"
                        data-testid="tw-research-candidate-provenance-advanced"
                      >
                        {t("research.tw.candidateProvenanceAdvanced", {
                          candidateSetId: scan.candidateProvenance.candidateSetId,
                          version: String(scan.candidateProvenance.candidateSetVersion ?? ""),
                          snapshotId: scan.candidateProvenance.firstPassSnapshotId ?? "",
                        })}
                      </p>
                      {scan.candidateProvenance.firstPassDigest ? (
                        <p className="cds--helper-text-01">
                          {t("research.tw.candidateProvenanceDigest", {
                            digest: `${scan.candidateProvenance.firstPassDigest.slice(0, 12)}…`,
                          })}
                        </p>
                      ) : null}
                    </AdvancedDetailsDisclosure>
                  </div>
                ) : null}
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
                    {t("research.tw.notVerified", { quality: plainScanQualityText })}
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
                <AdvancedDetailsDisclosure>
                  <TextInput
                    id="tw-research-scan-id"
                    data-testid="tw-research-scan-id"
                    labelText={t("research.tw.scanId")}
                    value={scan.scanId}
                    readOnly
                  />
                  <p
                    className="cds--helper-text-01"
                    data-testid="tw-research-scan-raw-status"
                  >
                    {t("research.tw.scanRawStatus", {
                      status: displayStatus || "—",
                      quality: displayQuality || "—",
                    })}
                  </p>
                  {manifestQuery.data ? (
                    <pre
                      className="cds--body-compact-01"
                      data-testid="tw-research-scan-manifest-json"
                      style={{ whiteSpace: "pre-wrap" }}
                    >
                      {JSON.stringify(manifestQuery.data, null, 2)}
                    </pre>
                  ) : null}
                </AdvancedDetailsDisclosure>
              </Stack>
            </Tile>
          ) : null}

          {scan ? (
            <TableContainer title={t("research.tw.ledgerTitle")}>
              <p className="cds--helper-text-01" data-testid="tw-research-stage-order">
                {t(
                  scan.stageOrder === "monthly_first"
                    ? "research.tw.stageOrderMonthlyFirst"
                    : "research.tw.stageOrderDailyFirst",
                )}
              </p>
              <p className="cds--helper-text-01">{t("research.tw.expandHint")}</p>
              <Table size="sm">
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("research.tw.expandDetails")}</TableHeader>
                    <TableHeader>{t("research.tw.colStock")}</TableHeader>
                    <TableHeader>{t("research.tw.colName")}</TableHeader>
                    <TableHeader>{t("research.tw.colStage")}</TableHeader>
                    <TableHeader>{t("research.tw.colTechnicalStage")}</TableHeader>
                    {scan.stageOrder === "monthly_first" ? (
                      <>
                        <TableHeader>{t("research.tw.colMonthlyFirst")}</TableHeader>
                        <TableHeader>{t("research.tw.colDailySecond")}</TableHeader>
                      </>
                    ) : (
                      <>
                        <TableHeader>{t("research.tw.colDailyFirst")}</TableHeader>
                        <TableHeader>{t("research.tw.colMonthlySecond")}</TableHeader>
                      </>
                    )}
                    <TableHeader>{t("research.tw.colBasis")}</TableHeader>
                    <TableHeader>{t("research.tw.colThemes")}</TableHeader>
                    <TableHeader>{t("research.tw.colReason")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {pageRows.map((row) => {
                    const daily = (row.daily as Record<string, unknown> | undefined) || {};
                    const monthly = (row.monthly as Record<string, unknown> | undefined) || {};
                    const themes = (row.theme_keys as string[] | undefined) || [];
                    const stockId = displayText(row.stock_id) || displayText(row.stockId);
                    const stockName =
                      displayText(row.stock_name) ||
                      displayText(row.stockName) ||
                      "";
                    const nameStatus =
                      displayText(row.name_status) || displayText(row.nameStatus);
                    const firstPass =
                      scan.stageOrder === "monthly_first"
                        ? displayText(monthly.passStatus)
                        : displayText(daily.passStatus);
                    const secondPass =
                      scan.stageOrder === "monthly_first"
                        ? displayText(
                            daily.executionStatus ?? daily.passStatus,
                          )
                        : displayText(monthly.passStatus);
                    const expanded = Boolean(expandedLedgerIds[stockId]);
                    const basis =
                      displayText(row.relativePriceBasis) ||
                      displayText(
                        (row.fields as { relative_price_basis?: { value?: string } } | undefined)
                          ?.relative_price_basis?.value,
                      );
                    return (
                      <Fragment key={stockId}>
                      <TableRow>
                        <TableCell>
                          <Button
                            kind="ghost"
                            size="sm"
                            data-testid={`tw-research-expand-${stockId}`}
                            onClick={() =>
                              setExpandedLedgerIds((current) => ({
                                ...current,
                                [stockId]: !current[stockId],
                              }))
                            }
                          >
                            {expanded ? "−" : "+"}
                          </Button>
                        </TableCell>
                        <TableCell>{stockId}</TableCell>
                        <TableCell>
                          {formatTwStockLabel(
                            stockId,
                            stockName,
                            nameStatus,
                            t("research.tw.nameUnavailable"),
                          )}
                        </TableCell>
                        <TableCell>{displayText(row.stage)}</TableCell>
                        <TableCell>
                          {displayText(row.technical_stage) ||
                            displayText(row.technicalStage) ||
                            "—"}
                        </TableCell>
                        <TableCell>{firstPass}</TableCell>
                        <TableCell>{secondPass}</TableCell>
                        <TableCell>{basis || "—"}</TableCell>
                        <TableCell>
                          {themes.length ? themes.join(", ") : t("research.tw.unclassified")}
                        </TableCell>
                        <TableCell>{displayText(row.rejection_reason)}</TableCell>
                      </TableRow>
                      {expanded ? (
                        <TableRow>
                          <TableCell colSpan={10}>
                            <div data-testid={`tw-research-detail-${stockId}`}>
                              {!pinnedFirstPassSnapshotId ? (
                                <p className="cds--helper-text-01">
                                  {t("research.tw.chipEvidenceNeedSnapshot")}
                                </p>
                              ) : frozenPageChipQuery.isPending ? (
                                <InlineLoading
                                  description={t("research.tw.chipEvidenceLoading")}
                                  status="active"
                                />
                              ) : frozenPageChipQuery.isError && !frozenPageChipQuery.data ? (
                                <RequestStateNotice
                                  kind={errorToNoticeKind(frozenPageChipQuery.error)}
                                  scopeLabel={t("research.tw.chipEvidenceTitle")}
                                  status={toApiError(frozenPageChipQuery.error).status}
                                  detail={describeResearchFailure(frozenPageChipQuery.error)}
                                  onRetry={() => {
                                    void frozenPageChipQuery.refetch();
                                  }}
                                />
                              ) : (
                                <ChipEvidenceCard
                                  row={
                                    frozenChipByStockId.get(stockId) ?? {
                                      stockId,
                                      status: "not_fetched",
                                      reason: "stock_not_in_first_pass_snapshot",
                                      notBuySignal: true,
                                    }
                                  }
                                  staleCacheBanner={
                                    frozenPageChipQuery.isError && frozenPageChipQuery.data
                                      ? {
                                          capturedAt:
                                            frozenChipByStockId.get(stockId)?.institutional
                                              ?.capturedAt ?? null,
                                        }
                                      : null
                                  }
                                />
                              )}
                              <pre
                                className="cds--body-compact-01"
                                style={{ whiteSpace: "pre-wrap", margin: 0 }}
                              >
                                {JSON.stringify(
                                  {
                                    coverage: row.coverage ?? null,
                                    monthly,
                                    daily,
                                    readiness: row.readiness ?? null,
                                  },
                                  null,
                                  2,
                                )}
                              </pre>
                            </div>
                          </TableCell>
                        </TableRow>
                      ) : null}
                      </Fragment>
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
              <AdvancedDetailsDisclosure>
                <TextArea
                  id="tw-research-latest-manifest-json"
                  data-testid="tw-research-latest-manifest-json"
                  labelText={t("research.tw.latestManifestJson")}
                  value={latestManifestText}
                  readOnly
                  rows={12}
                />
                <p
                  className="cds--helper-text-01"
                  data-testid="tw-research-build-version"
                >
                  {t("research.tw.buildVersion", {
                    sha: process.env.NEXT_PUBLIC_BUILD_SHA || "unknown",
                    time: process.env.NEXT_PUBLIC_BUILD_TIME || "unknown",
                  })}
                </p>
              </AdvancedDetailsDisclosure>
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
                <AdvancedDetailsDisclosure>
                  <TextArea
                    id="tw-research-agent-json"
                    labelText={t("research.tw.agentJson")}
                    value={agentText}
                    readOnly
                    rows={16}
                  />
                </AdvancedDetailsDisclosure>
              </Stack>
            </Tile>
          ) : null}
                </Stack>
              </TabPanel>
            </TabPanels>
          </Tabs>
          </div>
        </Stack>
      </Column>
    </Grid>
  );
}
