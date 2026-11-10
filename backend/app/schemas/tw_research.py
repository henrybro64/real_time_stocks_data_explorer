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


class ResearchContractResponse(BaseModel):
    contractVersion: str
    schemaVersion: str
    calculationVersion: str
    universePresetVersion: str
    priceBasis: str
    industryAgnostic: bool = True
    starterPresetIsOptional: bool = True
    noHardUniverseCap: bool = True
    fieldMap: list[FieldProvenance]
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
