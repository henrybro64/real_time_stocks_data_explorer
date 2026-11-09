"""Optional starter 14-sector Taiwan theme preset (#310).

This is a seed/pilot configuration only. It is not a product maximum, not an
admission whitelist, and not required input for scans. Any catalog TWSE/TPEx
symbol remains eligible whether or not it appears here.
"""

from __future__ import annotations

from app.services.stock_theme_models import ThemeAssignment, ThemeDefinition

# Theme keys are stable English slugs; labels keep the operator-facing Chinese names.
TW_RESEARCH_THEME_DEFINITIONS: tuple[ThemeDefinition, ...] = (
    ThemeDefinition(key="tw-packaging-testing", label="封測"),
    ThemeDefinition(key="tw-thermal", label="散熱"),
    ThemeDefinition(key="tw-cpo-optical-comms", label="CPO/光通訊"),
    ThemeDefinition(key="tw-pcb", label="PCB"),
    ThemeDefinition(key="tw-abf-substrate", label="ABF載板"),
    ThemeDefinition(key="tw-petrochemical", label="塑化"),
    ThemeDefinition(key="tw-fiberglass", label="玻纖布"),
    ThemeDefinition(key="tw-ccl", label="CCL"),
    ThemeDefinition(key="tw-ic-design", label="IC設計"),
    ThemeDefinition(key="tw-server-odm", label="伺服器代工"),
    ThemeDefinition(key="tw-machine-tools-robotics", label="工具機/機器人"),
    ThemeDefinition(key="tw-optics", label="光學"),
    ThemeDefinition(key="tw-panel", label="面板"),
    ThemeDefinition(key="tw-semiconductor-equipment", label="半導體設備"),
)

# Multi-theme overlap is intentional (e.g. PCB/CCL). Membership is metadata only.
TW_RESEARCH_THEME_ASSIGNMENTS: tuple[ThemeAssignment, ...] = (
    # 封測
    ThemeAssignment("TW:3711", ("tw-packaging-testing",)),
    ThemeAssignment("TW:6239", ("tw-packaging-testing",)),
    ThemeAssignment("TW:3374", ("tw-packaging-testing",)),
    ThemeAssignment("TW:2329", ("tw-packaging-testing",)),
    ThemeAssignment("TW:2449", ("tw-packaging-testing",)),
    ThemeAssignment("TW:6269", ("tw-packaging-testing",)),
    # 散熱
    ThemeAssignment("TW:3017", ("tw-thermal",)),
    ThemeAssignment("TW:3324", ("tw-thermal",)),
    ThemeAssignment("TW:6235", ("tw-thermal",)),
    ThemeAssignment("TW:2421", ("tw-thermal",)),
    ThemeAssignment("TW:3653", ("tw-thermal", "tw-semiconductor-equipment")),
    # CPO/光通訊
    ThemeAssignment("TW:3406", ("tw-cpo-optical-comms", "tw-optics")),
    ThemeAssignment("TW:3363", ("tw-cpo-optical-comms",)),
    ThemeAssignment("TW:4979", ("tw-cpo-optical-comms",)),
    ThemeAssignment("TW:3081", ("tw-cpo-optical-comms",)),
    ThemeAssignment("TW:6451", ("tw-cpo-optical-comms",)),
    # PCB (overlap with CCL where applicable)
    ThemeAssignment("TW:3037", ("tw-pcb",)),
    ThemeAssignment("TW:2313", ("tw-pcb",)),
    ThemeAssignment("TW:2355", ("tw-pcb",)),
    ThemeAssignment("TW:5469", ("tw-pcb",)),
    ThemeAssignment("TW:2368", ("tw-pcb",)),
    ThemeAssignment("TW:6213", ("tw-pcb", "tw-ccl")),
    # ABF載板
    ThemeAssignment("TW:8046", ("tw-abf-substrate",)),
    ThemeAssignment("TW:3189", ("tw-abf-substrate",)),
    ThemeAssignment("TW:6274", ("tw-abf-substrate", "tw-ccl")),
    ThemeAssignment("TW:4958", ("tw-abf-substrate",)),
    # 塑化
    ThemeAssignment("TW:1301", ("tw-petrochemical",)),
    ThemeAssignment("TW:1303", ("tw-petrochemical",)),
    ThemeAssignment("TW:1326", ("tw-petrochemical",)),
    ThemeAssignment("TW:6505", ("tw-petrochemical",)),
    ThemeAssignment("TW:1314", ("tw-petrochemical",)),
    # 玻纖布
    ThemeAssignment("TW:1477", ("tw-fiberglass",)),
    ThemeAssignment("TW:1815", ("tw-fiberglass",)),
    ThemeAssignment("TW:4306", ("tw-fiberglass",)),
    # CCL
    ThemeAssignment("TW:2383", ("tw-ccl", "tw-pcb")),
    ThemeAssignment("TW:8358", ("tw-ccl",)),
    ThemeAssignment("TW:6202", ("tw-ccl",)),
    # IC設計
    ThemeAssignment("TW:2454", ("tw-ic-design",)),
    ThemeAssignment("TW:3034", ("tw-ic-design",)),
    ThemeAssignment("TW:2379", ("tw-ic-design",)),
    ThemeAssignment("TW:3661", ("tw-ic-design",)),
    ThemeAssignment("TW:2303", ("tw-ic-design",)),
    ThemeAssignment("TW:8299", ("tw-ic-design",)),
    ThemeAssignment("TW:4966", ("tw-ic-design",)),
    ThemeAssignment("TW:6643", ("tw-ic-design",)),
    ThemeAssignment("TW:3529", ("tw-ic-design",)),
    ThemeAssignment("TW:5274", ("tw-ic-design",)),
    # 伺服器代工
    ThemeAssignment("TW:2356", ("tw-server-odm",)),
    ThemeAssignment("TW:2324", ("tw-server-odm",)),
    ThemeAssignment("TW:3231", ("tw-server-odm",)),
    ThemeAssignment("TW:6669", ("tw-server-odm",)),
    ThemeAssignment("TW:2382", ("tw-server-odm",)),
    ThemeAssignment("TW:3706", ("tw-server-odm",)),
    ThemeAssignment("TW:2317", ("tw-server-odm",)),
    # 工具機/機器人
    ThemeAssignment("TW:2049", ("tw-machine-tools-robotics",)),
    ThemeAssignment("TW:1590", ("tw-machine-tools-robotics",)),
    ThemeAssignment("TW:6605", ("tw-machine-tools-robotics",)),
    ThemeAssignment("TW:4526", ("tw-machine-tools-robotics",)),
    ThemeAssignment("TW:1536", ("tw-machine-tools-robotics",)),
    # 光學
    ThemeAssignment("TW:3008", ("tw-optics",)),
    ThemeAssignment("TW:3450", ("tw-optics",)),
    ThemeAssignment("TW:2395", ("tw-optics",)),
    ThemeAssignment("TW:3376", ("tw-optics",)),
    # 面板
    ThemeAssignment("TW:2409", ("tw-panel",)),
    ThemeAssignment("TW:3481", ("tw-panel",)),
    ThemeAssignment("TW:6116", ("tw-panel",)),
    ThemeAssignment("TW:4935", ("tw-panel",)),
    # 半導體設備
    ThemeAssignment("TW:3583", ("tw-semiconductor-equipment",)),
    ThemeAssignment("TW:3131", ("tw-semiconductor-equipment",)),
    ThemeAssignment("TW:6196", ("tw-semiconductor-equipment",)),
    ThemeAssignment("TW:2465", ("tw-semiconductor-equipment",)),
    ThemeAssignment("TW:3532", ("tw-semiconductor-equipment",)),
    ThemeAssignment("TW:6488", ("tw-semiconductor-equipment",)),
)


def starter_preset_stock_ids() -> tuple[str, ...]:
    """Deduplicated canonical IDs in the optional starter preset."""
    seen: set[str] = set()
    ordered: list[str] = []
    for assignment in TW_RESEARCH_THEME_ASSIGNMENTS:
        if assignment.stock_id in seen:
            continue
        seen.add(assignment.stock_id)
        ordered.append(assignment.stock_id)
    return tuple(ordered)


def starter_preset_theme_keys() -> tuple[str, ...]:
    return tuple(theme.key for theme in TW_RESEARCH_THEME_DEFINITIONS)
