"""Taiwan equity research helpers (#310–#312).

Industry-agnostic: any catalog TWSE/TPEx symbol is eligible. The 14-sector
starter list is an optional universe preset, not an admission whitelist.
"""

from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    CONTRACT_VERSION,
    ENRICHMENT_VERSION,
    RESEARCH_SCHEMA_VERSION,
    SCAN_BATCH_SIZE,
    UNIVERSE_PRESET_VERSION,
)

__all__ = [
    "CALCULATION_VERSION",
    "CONTRACT_VERSION",
    "ENRICHMENT_VERSION",
    "RESEARCH_SCHEMA_VERSION",
    "SCAN_BATCH_SIZE",
    "UNIVERSE_PRESET_VERSION",
]
