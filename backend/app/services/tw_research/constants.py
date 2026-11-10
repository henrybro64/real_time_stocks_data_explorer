"""Version and budget constants for Taiwan research (#310–#312)."""

from __future__ import annotations

# Bump when the research field contract or provenance rules change.
CONTRACT_VERSION = "2026-11-09.1"
RESEARCH_SCHEMA_VERSION = CONTRACT_VERSION

# Bump when MACD/KD/pass rules or monthly assembly change.
CALCULATION_VERSION = "macd12-26-9_kd9-3-3_v1"

# Bump when enrichment field provenance rules change (#313).
ENRICHMENT_VERSION = "enrich-v1"

# Enrichment operational budgets — not a global researchable-stock cap.
ENRICH_BATCH_SIZE = 10
DEFAULT_MAX_ENRICHMENTS_PER_RUN = 20
EXPORT_CHUNK_SIZE = 25

# Bump when the optional starter membership list changes.
UNIVERSE_PRESET_VERSION = "tw-starter-14-sectors-v1"

# Operational safety limits — not a total-universe cap (#312).
SCAN_BATCH_SIZE = 10
QUOTE_PROVIDER_CHUNK_SIZE = 20  # TWProvider.get_quotes_batch layer; distinct from SCAN_BATCH_SIZE
DEFAULT_EXTERNAL_WORKERS = 1
DEFAULT_RUN_TIMEOUT_SECONDS = 120
DEFAULT_MAX_UPSTREAM_MONTH_FETCHES = 40
# #329: each explicit resume opens another bounded month-fetch window.
DEFAULT_BUDGET_WINDOW_MONTH_FETCHES = 40
MAX_UPSTREAM_MONTH_FETCHES_CAP = 200
# Cluster-wide concurrent scan-batch leases (manual + scheduled). Not a universe cap.
DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES = 2
# Cross-pod history single-flight lease (#329). Short enough to recover after pod crash,
# renewed by a healthy leader so slow upstream work does not open a duplicate window.
HISTORY_FLIGHT_LEASE_SECONDS = 180
HISTORY_FLIGHT_RENEW_INTERVAL_SECONDS = 45
# Fail closed after this many consecutive renew DB errors (lease may already be stolen).
HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES = 3
HISTORY_FLIGHT_WAIT_MAX_SECONDS = 120
HISTORY_FLIGHT_POLL_INITIAL_SECONDS = 0.05
HISTORY_FLIGHT_POLL_MAX_SECONDS = 1.0
# Named coordination lock for atomic admission (#329).
SCAN_ADMISSION_LOCK_NAME = "scan_admission"
HISTORY_BUDGET_EXHAUSTED_REASON = "history_budget_exhausted"
DEFAULT_TARGET_COUNT = 10
# Schedule tick: bounded batches + enrichment windows per invocation (#330).
DEFAULT_SCHEDULE_MAX_BATCHES = 5
DEFAULT_SCHEDULE_MAX_ENRICH_BATCHES = 2
# Owner-level tick lock: short lease, heartbeated during long batches (#330).
SCHEDULE_TICK_LEASE_SECONDS = 120
SCHEDULE_TICK_RENEW_INTERVAL_SECONDS = 40
# Wall-clock budget for one CronJob tick (under k8s activeDeadlineSeconds=900).
DEFAULT_SCHEDULE_TICK_DEADLINE_SECONDS = 800
# Continue unfinished prior trading-day scans before opening a newer day (#330).
SCHEDULE_BACKLOG_MAX_CALENDAR_DAYS = 5
# Lightweight OHLCV readiness sample size after session close (#330).
SCHEDULE_DATA_READY_SAMPLE_SIZE = 3
INCOMPLETE_SCHEDULE_STATUSES = frozenset(
    {
        "queued",
        "running",
        "partial",
        "budget_exhausted",
        "completed_processing",
        # May still need enrichment after an early immutable snapshot.
        "verified_ready",
    }
)

# Indicator warm-up guidance (documented; enforced as insufficient_history).
# Daily MACD(12,26,9) needs roughly 26 + 9 completed sessions after seed.
MIN_DAILY_BARS_FOR_MACD_KD = 35
# Monthly MACD needs ~35 fully closed months; ~750 trading days is a lower bound.
# daily_price_history_service MAX=800 is not silently treated as enough (#311).
MIN_MONTHLY_BARS_FOR_MACD_KD = 35
PREFERRED_DAILY_HISTORY_LIMIT = 800

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
KD_PERIOD = 9
KD_K_SMOOTH = 3
KD_D_SMOOTH = 3

# Absolute tolerances when comparing against a fixed reference series in tests.
MACD_VALUE_TOLERANCE = 1e-6
KD_VALUE_TOLERANCE = 1e-6

PRICE_BASIS = "unadjusted_close"
