type TwResearchLanguage = "en" | "zh-TW";

// Tuple rows: [translationKey, English, Traditional Chinese]
const TW_RESEARCH_ROWS = [
  ["nav.twResearch", "TW research", "台股研究"],
  ["research.tw.eyebrow", "Private research", "私人研究"],
  ["research.tw.title", "Taiwan research scan", "台股研究掃描"],
  [
    "research.tw.subtitle",
    "Industry-agnostic scan, enrichment, and browser-readable export. Stock Ticker never writes Notion.",
    "產業中立掃描、enrichment 與瀏覽器可讀匯出。Stock Ticker 不會寫入 Notion。",
  ],
  ["research.tw.universeMode", "Universe selector", "宇宙選擇"],
  ["research.tw.modePreset", "Optional starter 14-sector preset", "可選 14 產業起始預設"],
  ["research.tw.modeThemes", "Optional theme / sector keys", "可選主題／產業鍵"],
  ["research.tw.modeIds", "Explicit canonical stock IDs", "明確股票代號"],
  [
    "research.tw.stockIds",
    "Stock IDs (any valid TWSE/TPEx symbols)",
    "股票代號（任何有效上市櫃代號）",
  ],
  ["research.tw.themeKeys", "Theme keys (one per line)", "主題鍵（每行一個）"],
  [
    "research.tw.themeKeysHint",
    "Optional filters only — not an allowlist. Available TW keys: {keys}",
    "僅為選用篩選，不是白名單。可用台股鍵：{keys}",
  ],
  [
    "research.tw.targetCount",
    "Output target (does not relax pass rules)",
    "輸出目標（不放寬通過條件）",
  ],
  ["research.tw.createScan", "Create scan", "建立掃描"],
  ["research.tw.runBatch", "Run scan batch", "執行掃描批次"],
  ["research.tw.runEnrichment", "Run enrichment batch", "執行 enrichment 批次"],
  ["research.tw.refresh", "Refresh snapshot", "重新整理快照"],
  ["research.tw.working", "Working…", "處理中…"],
  ["research.tw.scanId", "Scan ID", "掃描 ID"],
  [
    "research.tw.statusLine",
    "Status {status}. Processed {processed}/{universe} (remaining {remaining}). Day pass {dayPass}, dual-pass {qualified}. Target {target} is not padded.",
    "狀態 {status}。已處理 {processed}/{universe}（剩餘 {remaining}）。日線通過 {dayPass}，雙通過 {qualified}。目標 {target} 不會湊數。",
  ],
  [
    "research.tw.targetNote",
    "If fewer stocks pass than the target, the actual pass count is shown. Missing themes are Unclassified.",
    "若通過數少於目標，顯示實際通過數。沒有主題的股票顯示為未分類。",
  ],
  ["research.tw.ledgerTitle", "Scan ledger (paginated)", "掃描帳本（分頁）"],
  ["research.tw.colStock", "Stock", "股票"],
  ["research.tw.colStage", "Stage", "階段"],
  ["research.tw.colDaily", "Daily", "日線"],
  ["research.tw.colMonthly", "Monthly", "月線"],
  ["research.tw.colThemes", "Themes", "主題"],
  ["research.tw.colReason", "Reason", "原因"],
  ["research.tw.unclassified", "Unclassified", "未分類"],
  ["research.tw.loadAgentJson", "Load Agent JSON", "載入 Agent JSON"],
  ["research.tw.prevChunk", "Previous chunk", "上一塊"],
  ["research.tw.nextChunk", "Next chunk", "下一塊"],
  ["research.tw.loadChunkOnly", "Load chunk JSON only", "只載入 chunk JSON"],
  [
    "research.tw.agentChunkStatus",
    "Agent handoff chunk {index} of {total}",
    "Agent 交接 chunk {index}／{total}",
  ],
  ["research.tw.downloadCsv", "Download CSV", "下載 CSV"],
  [
    "research.tw.agentHandoffNote",
    "frontend_export_ready. External Notion publish is Agent-owned and optional (#315). Walk every chunk for full ledger coverage.",
    "frontend_export_ready。外部 Notion 發佈由 Agent 負責，且為選用（#315）。請走完每個 chunk 以取得完整帳本。",
  ],
  ["research.tw.agentJson", "Browser-readable export JSON", "瀏覽器可讀匯出 JSON"],
  ["research.tw.errorTitle", "Taiwan research request failed", "台股研究請求失敗"],
] as const satisfies ReadonlyArray<readonly [string, string, string]>;

export type TwResearchTranslationKey = (typeof TW_RESEARCH_ROWS)[number][0];

export function twResearchMessages(
  language: TwResearchLanguage,
): Record<TwResearchTranslationKey, string> {
  const valueIndex = language === "en" ? 1 : 2;
  return Object.fromEntries(
    TW_RESEARCH_ROWS.map((row) => [row[0], row[valueIndex]]),
  ) as Record<TwResearchTranslationKey, string>;
}
