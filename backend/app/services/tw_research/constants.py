"""Version and budget constants for Taiwan research (#310–#312)."""

from __future__ import annotations

# Bump when the research field contract or provenance rules change.
CONTRACT_VERSION = "2026-11-08.1"
RESEARCH_SCHEMA_VERSION = CONTRACT_VERSION

# Bump when MACD/KD/pass rules or monthly assembly change.
CALCULATION_VERSION = "macd12-26-9_kd9-3-3_v1"

# Bump when the optional starter membership list changes.
UNIVERSE_PRESET_VERSION = "tw-starter-14-sectors-v1"

# Operational safety limits — not a total-universe cap (#312).
SCAN_BATCH_SIZE = 10
QUOTE_PROVIDER_CHUNK_SIZE = 20  # TWProvider.get_quotes_batch layer; distinct from SCAN_BATCH_SIZE
DEFAULT_EXTERNAL_WORKERS = 1
DEFAULT_RUN_TIMEOUT_SECONDS = 120
DEFAULT_MAX_UPSTREAM_MONTH_FETCHES = 40
DEFAULT_TARGET_COUNT = 10

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
