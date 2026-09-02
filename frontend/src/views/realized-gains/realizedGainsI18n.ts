type RealizedGainsLanguage = "en" | "zh-TW";

type BilingualCopy = Record<RealizedGainsLanguage, string>;

const REALIZED_GAINS_COPY = {
  "nav.realizedGains": {
    en: "Realized P/L",
    "zh-TW": "已實現損益",
  },
  "realizedGains.title": {
    en: "Realized P/L",
    "zh-TW": "已實現損益",
  },
  "realizedGains.description": {
    en: "Record completed trades manually and review realized gains by currency without mixing TWD and USD.",
    "zh-TW": "手動記錄已完成的交易，並依幣別分開檢視已實現損益，不混合 TWD 與 USD。",
  },
  "realizedGains.currency.label": {
    en: "Currency",
    "zh-TW": "幣別",
  },
  "realizedGains.currency.twd": {
    en: "TWD (NT$)",
    "zh-TW": "TWD (NT$)",
  },
  "realizedGains.currency.usd": {
    en: "USD (US$)",
    "zh-TW": "USD (US$)",
  },
  "realizedGains.period.1M": {
    en: "Past 1 month",
    "zh-TW": "近 1 個月",
  },
  "realizedGains.period.3M": {
    en: "Past 3 months",
    "zh-TW": "近 3 個月",
  },
  "realizedGains.period.6M": {
    en: "Past 6 months",
    "zh-TW": "近 6 個月",
  },
  "realizedGains.period.1Y": {
    en: "Past 1 year",
    "zh-TW": "近 1 年",
  },
  "realizedGains.chart.title": {
    en: "Monthly realized P/L (last 12 months)",
    "zh-TW": "近 12 個月每月已實現損益",
  },
  "realizedGains.form.title": {
    en: "Trade entry",
    "zh-TW": "交易輸入",
  },
  "realizedGains.form.createTitle": {
    en: "Add realized trade",
    "zh-TW": "新增已實現交易",
  },
  "realizedGains.form.editTitle": {
    en: "Edit realized trade",
    "zh-TW": "編輯已實現交易",
  },
  "realizedGains.form.symbol": {
    en: "Symbol",
    "zh-TW": "代號",
  },
  "realizedGains.form.buyPrice": {
    en: "Buy price",
    "zh-TW": "買入價",
  },
  "realizedGains.form.sellPrice": {
    en: "Sell price",
    "zh-TW": "賣出價",
  },
  "realizedGains.form.shares": {
    en: "Shares",
    "zh-TW": "股數",
  },
  "realizedGains.form.buyDate": {
    en: "Buy date (optional)",
    "zh-TW": "買入日期（選填）",
  },
  "realizedGains.form.sellDate": {
    en: "Sell date",
    "zh-TW": "賣出日期",
  },
  "realizedGains.form.submitCreate": {
    en: "Save trade",
    "zh-TW": "儲存交易",
  },
  "realizedGains.form.submitUpdate": {
    en: "Save changes",
    "zh-TW": "儲存變更",
  },
  "realizedGains.form.cancel": {
    en: "Cancel",
    "zh-TW": "取消",
  },
  "realizedGains.preview.gain": {
    en: "Estimated realized P/L",
    "zh-TW": "預估已實現損益",
  },
  "realizedGains.preview.return": {
    en: "Estimated return",
    "zh-TW": "預估報酬率",
  },
  "realizedGains.history.title": {
    en: "Trade history",
    "zh-TW": "交易紀錄",
  },
  "realizedGains.loading": {
    en: "Loading realized trades",
    "zh-TW": "載入已實現交易中",
  },
  "realizedGains.empty.twd": {
    en: "No TWD realized trades yet.",
    "zh-TW": "尚無 TWD 已實現交易紀錄",
  },
  "realizedGains.empty.usd": {
    en: "No USD realized trades yet.",
    "zh-TW": "尚無 USD 已實現交易紀錄",
  },
  "realizedGains.column.sellDate": {
    en: "Sell date",
    "zh-TW": "賣出日期",
  },
  "realizedGains.column.symbol": {
    en: "Symbol",
    "zh-TW": "代號",
  },
  "realizedGains.column.buyPrice": {
    en: "Buy price",
    "zh-TW": "買入價",
  },
  "realizedGains.column.sellPrice": {
    en: "Sell price",
    "zh-TW": "賣出價",
  },
  "realizedGains.column.shares": {
    en: "Shares",
    "zh-TW": "股數",
  },
  "realizedGains.column.returnPercent": {
    en: "Return %",
    "zh-TW": "報酬率",
  },
  "realizedGains.column.realizedGain": {
    en: "Realized P/L",
    "zh-TW": "已實現損益",
  },
  "realizedGains.column.actions": {
    en: "Actions",
    "zh-TW": "操作",
  },
  "realizedGains.actions.edit": {
    en: "Edit",
    "zh-TW": "編輯",
  },
  "realizedGains.actions.delete": {
    en: "Delete",
    "zh-TW": "刪除",
  },
  "realizedGains.deleteConfirmTitle": {
    en: "Delete realized trade",
    "zh-TW": "刪除已實現交易",
  },
  "realizedGains.deleteConfirmMessage": {
    en: "Delete this realized trade record?",
    "zh-TW": "確定要刪除這筆已實現交易紀錄嗎？",
  },
  "realizedGains.deleteConfirmCancel": {
    en: "Cancel",
    "zh-TW": "取消",
  },
  "realizedGains.deleteConfirmAction": {
    en: "Delete",
    "zh-TW": "刪除",
  },
} satisfies Record<string, BilingualCopy>;

export type RealizedGainsTranslationKey = keyof typeof REALIZED_GAINS_COPY;

export function realizedGainsMessages(
  language: RealizedGainsLanguage,
): Record<RealizedGainsTranslationKey, string> {
  return Object.fromEntries(
    Object.entries(REALIZED_GAINS_COPY).map(([key, copy]) => [key, copy[language]]),
  ) as Record<RealizedGainsTranslationKey, string>;
}
