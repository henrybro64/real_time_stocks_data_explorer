"""Versioned Taiwan research request/response schemas (#310–#312)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FieldProvenance(BaseModel):
    field: str
    source: str
    source_as_of: str | None = None
    captured_at: str | None = None
    stale: bool = False
    missing: bool = False
    validation: str | None = None
    error: str | None = None
    freshness_class: Literal[
        "quote_or_eod_day",
        "monthly_closed_month",
        "tdcc_weekly",
        "earnings_quarterly",
        "computed",
        "config",
    ]
    # #335 contract audit extensions (optional so older rows remain valid).
    status: (
        Literal["implemented", "partial", "unavailable", "not_implemented", "deferred"] | None
    ) = None
    unit: str | None = None
    required: bool = False
    sector_applicability: str | None = None
    implementation_notes: str | None = None


class ResearchContractResponse(BaseModel):
    contractVersion: str
    schemaVersion: str
    calculationVersion: str
    universePresetVersion: str
    priceBasis: str
    firstPassVersion: str | None = None
    relativePriceBasisVersion: str | None = None
    industryAgnostic: bool = True
    starterPresetIsOptional: bool = True
    noHardUniverseCap: bool = True
    fieldMap: list[FieldProvenance]
    feasibilityMatrix: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str]


class UniverseSelectorRequest(BaseModel):
    mode: Literal["starter_preset", "theme_keys", "stock_ids"] = "starter_preset"
    themeKeys: list[str] = Field(default_factory=list)
    stockIds: list[str] = Field(default_factory=list)


class RejectedStockResponse(BaseModel):
    stockId: str
    reason: str


class ResolvedUniverseResponse(BaseModel):
    universeVersion: str
    mode: str
    stockIds: list[str]
    uniqueCount: int
    rejected: list[RejectedStockResponse]
    themeKeysApplied: list[str]
    notes: list[str]


class CreateScanRequest(BaseModel):
    universe: UniverseSelectorRequest = Field(default_factory=UniverseSelectorRequest)
    targetCount: Literal[10, 20] = 10
    tradingDay: str | None = None
    maxUpstreamMonthFetches: int = Field(default=40, ge=0, le=200)
    maxEnrichmentsPerRun: int = Field(default=20, ge=0, le=200)


class RunBatchRequest(BaseModel):
    scanId: str
    batchSize: int = Field(default=10, ge=1, le=10)
    timeoutSeconds: int = Field(default=120, ge=1, le=600)


class RunEnrichmentBatchRequest(BaseModel):
    scanId: str
    batchSize: int = Field(default=10, ge=1, le=10)


class ResumeBudgetWindowRequest(BaseModel):
    """Open another bounded history-fetch window for unresolved symbols (#329)."""

    scanId: str
    additionalMonthFetches: int = Field(default=40, ge=1, le=40)


class ScheduleRunRequest(BaseModel):
    """Internal post-market schedule tick (#330). Requires capture token auth."""

    dryRun: bool = False
    maxBatches: int = Field(default=5, ge=1, le=20)
    targetCount: Literal[10, 20] = 10
    tradingDay: str | None = None
    resumeIfExhausted: bool = True


class ScheduleRunResponse(BaseModel):
    outcome: str
    tradingDay: str
    scanId: str | None = None
    status: str | None = None
    qualityStatus: str | None = None
    batchesRun: int = 0
    enrichBatchesRun: int = 0
    resumed: bool = False
    notes: list[str] = Field(default_factory=list)
    dryRun: bool = False


class FirstPassDrainRequest(BaseModel):
    """Internal durable first-pass drain tick (#349). Token auth; disabled by default."""

    dryRun: bool = False
    maxBatches: int = Field(default=1, ge=1, le=8)
    batchSize: int = Field(default=5, ge=1, le=25)
    # Wall-clock budget per batch (budget 60 < curl 150 < lease 300; renew per symbol).
    timeBudgetSeconds: float = Field(default=60, ge=30, le=120)
    jobId: str | None = None


class FirstPassDrainResponse(BaseModel):
    outcome: str
    userId: str
    researchRunId: str | None = None
    jobId: str | None = None
    phase: str | None = None
    status: str | None = None
    scheduleEnabled: bool = False
    batchesRun: int = 0
    symbolsProcessed: int = 0
    waitingForRetry: bool = False
    leaseHeld: bool = False
    alreadyComplete: bool = False
    dryRun: bool = False
    notes: list[str] = Field(default_factory=list)
    stopsAtAnalystGate: bool = True
    notionWrites: bool = False
    phase2AutoStart: bool = False


class ResearchRunDrainControlRequest(BaseModel):
    """Operator pause/resume for durable first-pass drain (#349)."""

    enabled: bool
    notes: list[str] = Field(default_factory=list)


class ScanSnapshotResponse(BaseModel):
    scan: dict[str, Any]


class ScanSummaryResponse(BaseModel):
    scanId: str
    tradingDay: str
    universeVersion: str
    status: str
    qualityStatus: str
    targetCount: int
    universeSize: int
    cursor: int
    remainingCount: int
    updatedAt: str
    activeSnapshotId: str | None = None
    workerActivity: Literal["active", "unknown", "idle"]
    selectorMode: str
    themeKeys: list[str] = Field(default_factory=list)
    stockIds: list[str] = Field(default_factory=list)


class ScanListResponse(BaseModel):
    scans: list[ScanSummaryResponse]


class ScanProgressResponse(BaseModel):
    """Counters-only progress. Does not include the symbol ledger (#327)."""

    progress: dict[str, Any]


class ExportManifestResponse(BaseModel):
    manifest: dict[str, Any]


class ExportChunkResponse(BaseModel):
    chunk: dict[str, Any]


class FirstPassBatchRequest(BaseModel):
    """Bounded first-pass evidence collection (#335/#336). No CronJob/deploy."""

    universe: UniverseSelectorRequest = Field(default_factory=UniverseSelectorRequest)
    stockIds: list[str] = Field(default_factory=list)
    batchLimit: int = Field(default=10, ge=1, le=25)
    includeNonofficialMargins: bool = False
    # Default True: persist immutable snapshot (fail closed if DB unavailable).
    persist: bool = True


class FirstPassBatchResponse(BaseModel):
    result: dict[str, Any]


class ResearchMembershipSaveRequest(BaseModel):
    expectedVersion: int = Field(ge=0)
    themeKeys: list[str] | None = None
    disabledThemeKeys: list[str] | None = None
    sectorAdditions: dict[str, list[str]] | None = None
    sectorRemovals: dict[str, list[str]] | None = None
    customSectors: list[dict[str, Any]] | None = None
    extraStockIds: list[str] | None = None
    computeSubsetIds: list[str] | None = None
    resetToStarter: bool = False


class ResearchMembershipPreviewRequest(BaseModel):
    themeKeys: list[str] | None = None
    disabledThemeKeys: list[str] | None = None
    sectorAdditions: dict[str, list[str]] | None = None
    sectorRemovals: dict[str, list[str]] | None = None
    customSectors: list[dict[str, Any]] | None = None
    extraStockIds: list[str] | None = None
    computeSubsetIds: list[str] | None = None


class ResearchMembershipResponse(BaseModel):
    membership: dict[str, Any]


class FirstPassJobCreateRequest(BaseModel):
    stockIds: list[str] = Field(default_factory=list)
    useMembership: bool = True


class FirstPassJobBatchRequest(BaseModel):
    batchSize: int = Field(default=5, ge=1, le=25)
    includeNonofficialMargins: bool = False
    # Same default as drain — sits behind the ~150s frontend proxy.
    timeBudgetSeconds: float = Field(default=60, ge=30, le=120)


class FirstPassJobResponse(BaseModel):
    job: dict[str, Any]


class FirstPassJobProgressResponse(BaseModel):
    progress: dict[str, Any]


class FirstPassJobSymbolsResponse(BaseModel):
    result: dict[str, Any]


class RelativePriceBasisRequest(BaseModel):
    stockId: str
    officialPe: float | None = None


class RelativePriceBasisResponse(BaseModel):
    evidence: dict[str, Any]


class CandidateSetSubmitRequest(BaseModel):
    """Analyst/manual technical compute subset pinned to a first-pass digest (#336)."""

    firstPassSnapshotId: str
    firstPassDigest: str
    stockIds: list[str] = Field(default_factory=list)
    origin: Literal["manual", "analyst"] = "analyst"
    expectedVersion: int = Field(default=0, ge=0)
    selectionReasons: dict[str, Any] = Field(default_factory=dict)
    # Optional bounded daily MACD/KD even when monthly gate fails (#338).
    dailyOverrideIds: list[str] = Field(default_factory=list)
    decisionDate: str | None = None
    notes: list[str] = Field(default_factory=list)


class CandidateSetResponse(BaseModel):
    candidateSet: dict[str, Any]


class CandidateTechnicalScanRequest(BaseModel):
    """Start monthly/daily technical scan from an approved candidate set (#336)."""

    expectedVersion: int | None = Field(default=None, ge=0)
    candidateSetId: str | None = None
    targetCount: Literal[10, 20] = 10
    tradingDay: str | None = None
    maxUpstreamMonthFetches: int | None = Field(default=None, ge=0)
    maxEnrichmentsPerRun: int | None = Field(default=None, ge=0)
    # Bounded analyst override: compute daily despite monthly fail (#338).
    dailyOverrideIds: list[str] = Field(default_factory=list)


class ResearchRunStartRequest(BaseModel):
    """Manual full-research orchestration start (#341). Never auto-publishes."""

    phase: Literal["phase1_first_pass", "phase2_technical"] = "phase1_first_pass"
    tradingDay: str | None = None
    notes: list[str] = Field(default_factory=list)


class ResearchRunResponse(BaseModel):
    researchRun: dict[str, Any]


class ReportEvidenceRequest(BaseModel):
    """Build analyst-ready evidence pack for external ChatGPT (#340)."""

    firstPassSnapshotId: str | None = None
    scanId: str | None = None
    tradingDay: str | None = None


class ReportEvidenceResponse(BaseModel):
    evidence: dict[str, Any]


class FirstPassJobListResponse(BaseModel):
    jobs: list[dict[str, Any]]
