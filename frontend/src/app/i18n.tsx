"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Stock } from "@shared/stock";

export type AppLanguage = "en" | "zh-TW";

type TranslationKey =
  | "nav.dashboard"
  | "nav.menu.open"
  | "nav.menu.close"
  | "header.notifications"
  | "header.userProfile"
  | "header.language"
  | "language.english"
  | "language.traditionalChinese"
  | "home.hero.title"
  | "home.hero.description"
  | "home.summary.title"
  | "home.summary.markets"
  | "home.summary.tracked"
  | "home.summary.loading"
  | "marketStatus.title"
  | "marketStatus.syncCount"
  | "marketStatus.loading"
  | "marketStatus.open"
  | "marketStatus.closed"
  | "search.title"
  | "search.description"
  | "search.label"
  | "search.placeholder"
  | "search.loading"
  | "search.hint"
  | "search.emptyPrompt"
  | "search.noResults"
  | "watchlist.title"
  | "watchlist.count"
  | "watchlist.loading"
  | "watchlist.empty"
  | "stock.market"
  | "stock.updatedAt"
  | "stock.prevClose"
  | "stock.volume"
  | "stock.viewDetails"
  | "stock.remove"
  | "stock.add"
  | "stock.quoteLoadError"
  | "stock.quoteLoading"
  | "stock.weekRange"
  | "stock.weekRangeNoData"
  | "detail.invalidStockId"
  | "detail.loading"
  | "detail.selectHistoryRange"
  | "quote.updatedAt"
  | "quote.open"
  | "quote.high"
  | "quote.low"
  | "chart.noData"
  | "chart.title"
  | "chart.rangeSummary"
  | "chart.description";

const messages: Record<AppLanguage, Record<TranslationKey, string>> = {
  en: {
    "nav.dashboard": "Dashboard",
    "nav.menu.open": "Open navigation menu",
    "nav.menu.close": "Close navigation menu",
    "header.notifications": "Notifications",
    "header.userProfile": "User profile",
    "header.language": "Display language",
    "language.english": "English",
    "language.traditionalChinese": "Traditional Chinese",
    "home.hero.title": "Stock Watch Dashboard",
    "home.hero.description":
      "Track Taiwan and U.S. markets with a Carbon-powered workspace for search, watchlist, and live quote updates.",
    "home.summary.title": "Workspace summary",
    "home.summary.markets": "Markets",
    "home.summary.tracked": "Tracked",
    "home.summary.loading": "Syncing market data",
    "marketStatus.title": "Market status",
    "marketStatus.syncCount": "{count} markets synced",
    "marketStatus.loading": "Updating market status",
    "marketStatus.open": "Open",
    "marketStatus.closed": "Closed",
    "search.title": "Search stocks or symbols",
    "search.description": "Supports U.S. and Taiwan stock codes and company names.",
    "search.label": "Search stocks or symbols",
    "search.placeholder": "For example: AAPL, NVDA, 2330, TSMC",
    "search.loading": "Searching stocks",
    "search.hint": "Results update as you type. Add to watchlist without leaving this page.",
    "search.emptyPrompt": "Enter a symbol or company name to see stocks you can add to your watchlist.",
    "search.noResults": "No matching stocks found. Try a symbol or full company name.",
    "watchlist.title": "Watchlist",
    "watchlist.count": "{count} stocks",
    "watchlist.loading": "Loading watchlist quotes",
    "watchlist.empty": "Add stocks from search. Latest quotes and update times will appear here.",
    "stock.market": "Market",
    "stock.updatedAt": "Updated",
    "stock.prevClose": "Prev close",
    "stock.volume": "Volume",
    "stock.viewDetails": "View details",
    "stock.remove": "Remove",
    "stock.add": "Add",
    "stock.quoteLoadError": "Unable to load quote right now.",
    "stock.quoteLoading": "Loading quote",
    "stock.weekRange": "1W",
    "stock.weekRangeNoData": "No 1-week trend data yet",
    "detail.invalidStockId": "Stock ID not found.",
    "detail.loading": "Loading stock details",
    "detail.selectHistoryRange": "Select history range",
    "quote.updatedAt": "Updated {time}",
    "quote.open": "Open",
    "quote.high": "High",
    "quote.low": "Low",
    "chart.noData": "No chart data is available.",
    "chart.title": "Price history",
    "chart.rangeSummary": "{range} range {min} - {max}",
    "chart.description": "Chart shows closing-price trends for the selected range.",
  },
  "zh-TW": {
    "nav.dashboard": "儀表板",
    "nav.menu.open": "開啟導覽選單",
    "nav.menu.close": "關閉導覽選單",
    "header.notifications": "通知",
    "header.userProfile": "使用者設定",
    "header.language": "顯示語言",
    "language.english": "英文",
    "language.traditionalChinese": "繁體中文",
    "home.hero.title": "股票監看儀表板",
    "home.hero.description": "以 Carbon 設計系統打造工作區，一站掌握台股與美股搜尋、自選與即時報價更新。",
    "home.summary.title": "工作區摘要",
    "home.summary.markets": "市場數",
    "home.summary.tracked": "追蹤中",
    "home.summary.loading": "同步市場資料中",
    "marketStatus.title": "市場狀態",
    "marketStatus.syncCount": "同步 {count} 個市場",
    "marketStatus.loading": "更新市場狀態",
    "marketStatus.open": "開盤中",
    "marketStatus.closed": "已收盤",
    "search.title": "搜尋股票或代碼",
    "search.description": "支援美股與台股代碼、公司名稱。",
    "search.label": "搜尋股票或代碼",
    "search.placeholder": "例如 AAPL、NVDA、2330、台積電",
    "search.loading": "搜尋股票中",
    "search.hint": "輸入後即時更新結果，加入自選清單不需離開當前頁面。",
    "search.emptyPrompt": "輸入代碼或公司名稱後，這裡會顯示可加入自選清單的結果。",
    "search.noResults": "找不到符合條件的股票，請改用代碼或完整公司名稱。",
    "watchlist.title": "自選清單",
    "watchlist.count": "{count} 檔股票",
    "watchlist.loading": "載入自選報價中",
    "watchlist.empty": "搜尋股票後加入清單，這裡會持續顯示最新報價與更新時間。",
    "stock.market": "市場",
    "stock.updatedAt": "更新時間",
    "stock.prevClose": "前收",
    "stock.volume": "成交量",
    "stock.viewDetails": "檢視明細",
    "stock.remove": "移除",
    "stock.add": "加入",
    "stock.quoteLoadError": "目前無法載入報價。",
    "stock.quoteLoading": "載入報價中",
    "stock.weekRange": "近一週",
    "stock.weekRangeNoData": "近一週走勢暫時無資料",
    "detail.invalidStockId": "找不到股票代碼。",
    "detail.loading": "載入股票明細中",
    "detail.selectHistoryRange": "選擇歷史區間",
    "quote.updatedAt": "更新時間 {time}",
    "quote.open": "開盤",
    "quote.high": "最高",
    "quote.low": "最低",
    "chart.noData": "沒有可顯示的圖表資料。",
    "chart.title": "價格走勢",
    "chart.rangeSummary": "{range} 區間高低 {min} - {max}",
    "chart.description": "圖表顯示所選區間的收盤價走勢。",
  },
};

type LanguageContextValue = {
  language: AppLanguage;
  setLanguage: (nextLanguage: AppLanguage) => void;
  t: (key: TranslationKey, params?: Record<string, string | number>) => string;
  formatNumber: (value: number) => string;
};

const STORAGE_KEY = "stock-ticker-language";

const LanguageContext = createContext<LanguageContextValue | null>(null);

function interpolate(template: string, params: Record<string, string | number>) {
  return Object.entries(params).reduce(
    (result, [paramKey, paramValue]) => result.split(`{${paramKey}}`).join(String(paramValue)),
    template,
  );
}

function readStoredLanguage(): AppLanguage {
  if (typeof window === "undefined") {
    return "zh-TW";
  }
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "en" || stored === "zh-TW" ? stored : "zh-TW";
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<AppLanguage>("zh-TW");

  // Keep first client render aligned with SSR output, then apply saved preference.
  // This avoids hydration mismatch when localStorage language differs.
  useEffect(() => {
    setLanguageState(readStoredLanguage());
  }, []);

  const value = useMemo<LanguageContextValue>(
    () => ({
      language,
      setLanguage: (nextLanguage: AppLanguage) => {
        setLanguageState(nextLanguage);
        window.localStorage.setItem(STORAGE_KEY, nextLanguage);
      },
      t: (key: TranslationKey, params?: Record<string, string | number>) => {
        const template = messages[language][key];
        return params ? interpolate(template, params) : template;
      },
      formatNumber: (value: number) =>
        new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US").format(value),
    }),
    [language],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error("useLanguage must be used within a LanguageProvider");
  }
  return context;
}

export function getDisplayStockName(stock: Stock, language: AppLanguage): string {
  if (language === "zh-TW") {
    return stock.nameZhTw ?? stock.name ?? stock.nameEn ?? stock.symbol;
  }
  return stock.nameEn ?? stock.name ?? stock.nameZhTw ?? stock.symbol;
}
