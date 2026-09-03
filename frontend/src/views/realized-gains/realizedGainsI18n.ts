type RealizedGainsLanguage = "en" | "zh-TW";

// Tuple rows: [translationKey, English, Traditional Chinese]
const REALIZED_GAINS_ROWS = [
  ["nav.realizedGains", "Realized P/L", "已實現損益"],
  ["realizedGains.title", "Realized P/L", "已實現損益"],
  [
    "realizedGains.description",
    "Record completed trades manually and review realized gains by currency without mixing TWD and USD.",
    "手動記錄已完成的交易，並依幣別分開檢視已實現損益，不混合 TWD 與 USD。",
  ],
  ["realizedGains.currency.label", "Currency", "幣別"],
  ["realizedGains.currency.twd", "TWD (NT$)", "TWD (NT$)"],
  ["realizedGains.currency.usd", "USD (US$)", "USD (US$)"],
  ["realizedGains.period.1M", "Past 1 month", "近 1 個月"],
  ["realizedGains.period.3M", "Past 3 months", "近 3 個月"],
  ["realizedGains.period.6M", "Past 6 months", "近 6 個月"],
  ["realizedGains.period.1Y", "Past 1 year", "近 1 年"],
  [
    "realizedGains.chart.title",
    "Monthly realized P/L (last 12 months)",
    "近 12 個月每月已實現損益",
  ],
  ["realizedGains.form.title", "Trade entry", "交易輸入"],
  ["realizedGains.form.createTitle", "Add realized trade", "新增已實現交易"],
  ["realizedGains.form.editTitle", "Edit realized trade", "編輯已實現交易"],
  ["realizedGains.form.symbol", "Symbol", "代號"],
  ["realizedGains.form.buyPrice", "Buy price", "買入價"],
  ["realizedGains.form.sellPrice", "Sell price", "賣出價"],
  ["realizedGains.form.shares", "Shares", "股數"],
  ["realizedGains.form.cumulativeDividends", "Cumulative dividends", "累計配息"],
  [
    "realizedGains.form.cumulativeDividendsHelper",
    "Enter the total dividends received while holding this position.",
    "此欄位填入持有期間實際收到的配息總額。",
  ],
  ["realizedGains.form.buyDate", "Buy date (optional)", "買入日期（選填）"],
  ["realizedGains.form.sellDate", "Sell date", "賣出日期"],
  ["realizedGains.form.submitCreate", "Save trade", "儲存交易"],
  ["realizedGains.form.submitUpdate", "Save changes", "儲存變更"],
  ["realizedGains.form.cancel", "Cancel", "取消"],
  ["realizedGains.preview.priceGain", "Estimated price gain", "預估價差損益"],
  ["realizedGains.preview.dividends", "Estimated dividends", "預估累計配息"],
  ["realizedGains.preview.gain", "Estimated realized P/L", "預估已實現損益"],
  ["realizedGains.preview.return", "Estimated total return", "預估總報酬率"],
  ["realizedGains.history.title", "Trade history", "交易紀錄"],
  ["realizedGains.loading", "Loading realized trades", "載入已實現交易中"],
  ["realizedGains.empty.twd", "No TWD realized trades yet.", "尚無 TWD 已實現交易紀錄"],
  ["realizedGains.empty.usd", "No USD realized trades yet.", "尚無 USD 已實現交易紀錄"],
  ["realizedGains.column.sellDate", "Sell date", "賣出日期"],
  ["realizedGains.column.symbol", "Symbol", "代號"],
  ["realizedGains.column.buyPrice", "Buy price", "買入價"],
  ["realizedGains.column.sellPrice", "Sell price", "賣出價"],
  ["realizedGains.column.shares", "Shares", "股數"],
  ["realizedGains.column.dividends", "Dividends", "累計配息"],
  ["realizedGains.column.returnPercent", "Return %", "報酬率"],
  ["realizedGains.column.realizedGain", "Realized P/L", "已實現損益"],
  ["realizedGains.column.actions", "Actions", "操作"],
  ["realizedGains.actions.edit", "Edit", "編輯"],
  ["realizedGains.actions.delete", "Delete", "刪除"],
  ["realizedGains.deleteConfirmTitle", "Delete realized trade", "刪除已實現交易"],
  [
    "realizedGains.deleteConfirmMessage",
    "Delete this realized trade record?",
    "確定要刪除這筆已實現交易紀錄嗎？",
  ],
  ["realizedGains.deleteConfirmCancel", "Cancel", "取消"],
  ["realizedGains.deleteConfirmAction", "Delete", "刪除"],
] as const satisfies ReadonlyArray<readonly [string, string, string]>;

export type RealizedGainsTranslationKey = (typeof REALIZED_GAINS_ROWS)[number][0];

export function realizedGainsMessages(
  language: RealizedGainsLanguage,
): Record<RealizedGainsTranslationKey, string> {
  const valueIndex = language === "en" ? 1 : 2;
  return Object.fromEntries(
    REALIZED_GAINS_ROWS.map((row) => [row[0], row[valueIndex]]),
  ) as Record<RealizedGainsTranslationKey, string>;
}
