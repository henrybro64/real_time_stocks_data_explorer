"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Stock } from "@shared/stock";

export type AppLanguage = "en" | "zh-TW";

type TranslationKey =
  | "nav.dashboard"
  | "nav.markets"
  | "nav.rankings"
  | "nav.watchlist"
  | "nav.portfolio"
  | "nav.assets"
  | "nav.aiSettings"
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
  | "detail.backToDashboard"
  | "quote.updatedAt"
  | "quote.open"
  | "quote.high"
  | "quote.low"
  | "chart.noData"
  | "chart.title"
  | "chart.rangeSummary"
  | "chart.description"
  | "chart.axisYTitle"
  | "chart.axisDate"
  | "chart.candleClose"
  | "chart.interactHint"
  | "rankings.title"
  | "rankings.description"
  | "rankings.market"
  | "rankings.market.us"
  | "rankings.market.tw"
  | "rankings.loading"
  | "rankings.empty"
  | "rankings.tab.volume"
  | "rankings.tab.volumeUp"
  | "rankings.tab.volumeDown"
  | "rankings.tab.netBuy"
  | "rankings.tab.netSell"
  | "rankings.column.rank"
  | "rankings.column.symbol"
  | "rankings.column.name"
  | "rankings.column.lastPrice"
  | "rankings.column.changePercent"
  | "rankings.column.volume"
  | "rankings.column.volumeChangePercent"
  | "rankings.column.netBuySell"
  | "rankings.column.dataTime"
  | "rankings.column.source"
  | "rankings.notice.usScope"
  | "rankings.notice.twScope"
  | "rankings.notice.quoteUnofficial"
  | "rankings.notice.twDaily"
  | "rankings.notice.twInstitutionalUnavailable"
  | "rankings.freshness.quoteSnapshotOpen"
  | "rankings.freshness.quoteSnapshotClosed"
  | "rankings.freshness.volumeBaselineMissing"
  | "rankings.freshness.volumeBaselineOpen"
  | "rankings.freshness.volumeBaselineClosed"
  | "rankings.freshness.twInstitutionalUnavailable"
  | "rankings.freshness.twNetBuy"
  | "rankings.freshness.twNetSell"
  | "rankings.freshness.twNetBuyClosed"
  | "rankings.freshness.twNetSellClosed"
  | "rankings.source.quoteProviders"
  | "rankings.source.twse"
  | "rankings.source.unsupported"
  | "portfolio.title"
  | "portfolio.description"
  | "portfolio.equity.title"
  | "portfolio.count"
  | "portfolio.loading"
  | "portfolio.loadError"
  | "portfolio.empty"
  | "portfolio.table.currencyHint"
  | "portfolio.column.symbol"
  | "portfolio.column.name"
  | "portfolio.column.market"
  | "portfolio.column.quantity"
  | "portfolio.column.last"
  | "portfolio.column.marketValue"
  | "portfolio.column.change"
  | "portfolio.column.changePercent"
  | "portfolio.column.volume"
  | "portfolio.column.updated"
  | "portfolio.column.actions"
  | "portfolio.search.eyebrow"
  | "portfolio.search.title"
  | "portfolio.search.description"
  | "portfolio.search.hint"
  | "portfolio.search.emptyPrompt"
  | "portfolio.addHolding"
  | "portfolio.removeHolding"
  | "portfolio.quantity.saving"
  | "portfolio.quantity.saved"
  | "portfolio.assets.eyebrow"
  | "portfolio.assets.title"
  | "portfolio.assets.count"
  | "portfolio.assets.loading"
  | "portfolio.assets.empty"
  | "portfolio.assets.add"
  | "portfolio.assets.remove"
  | "portfolio.assets.column.type"
  | "portfolio.assets.column.name"
  | "portfolio.assets.column.currency"
  | "portfolio.assets.column.value"
  | "portfolio.assets.type.cash"
  | "portfolio.assets.type.realEstate"
  | "portfolio.assets.type.other"
  | "portfolio.aggregate.eyebrow"
  | "portfolio.aggregate.title"
  | "portfolio.aggregate.combinedUsd"
  | "portfolio.aggregate.combinedTwd"
  | "portfolio.aggregate.liquidAssets"
  | "portfolio.aggregate.fxRate"
  | "portfolio.history.loading"
  | "portfolio.history.empty"
  | "portfolio.history.range"
  | "portfolio.history.zoomIn"
  | "portfolio.history.zoomOut"
  | "portfolio.history.interactHint"
  | "portfolio.history.value"
  | "portfolio.history.session"
  | "portfolio.history.session.tw"
  | "portfolio.history.session.us"
  | "portfolio.history.holdingsUsd"
  | "portfolio.history.holdingsTwd"
  | "portfolio.history.combinedAssetsUsd"
  | "portfolio.history.combinedAssetsTwd"
  | "aiSettings.title"
  | "aiSettings.description"
  | "twInsights.title"
  | "twInsights.description"
  | "twInsights.loading"
  | "twInsights.revenue.title"
  | "twInsights.revenue.latest"
  | "twInsights.peer.title"
  | "twInsights.peer.period"
  | "twInsights.peer.company"
  | "twInsights.peer.industry"
  | "twInsights.peer.rank"
  | "twInsights.dividend.title"
  | "twInsights.dividend.avg2y"
  | "twInsights.dividend.year"
  | "twInsights.dividend.cash"
  | "twInsights.dividend.stock"
  | "twInsights.dividend.yield"
  | "twInsights.dividend.exDate"
  | "twInsights.dividend.payDate"
  | "twInsights.dividend.distributionTitle"
  | "twInsights.dividend.policyTitle"
  | "twInsights.dividend.byPayoutYear"
  | "twInsights.dividend.byEarningsYear"
  | "twInsights.dividend.earningsYear"
  | "keyStats.title"
  | "keyStats.open"
  | "keyStats.previousClose"
  | "keyStats.volume"
  | "keyStats.avgVolume10d"
  | "keyStats.marketCap"
  | "keyStats.dayRange"
  | "keyStats.week52Range"
  | "keyStats.low"
  | "keyStats.high"
  | "keyStats.sharesOutstanding"
  | "keyStats.beta"
  | "keyStats.peTtm"
  | "keyStats.epsTtm"
  | "keyStats.dividendYield"
  | "keyStats.exDividendDate"
  | "keyStats.dividendPayDate"
  | "keyStats.nextEarnings"
  | "keyStats.na"
  | "keyStats.infoHint"
  | "keyStats.fundamentalsUnavailable";

const messages: Record<AppLanguage, Record<TranslationKey, string>> = {
  en: {
    "nav.dashboard": "Dashboard",
    "nav.markets": "Markets",
    "nav.rankings": "Rankings",
    "nav.watchlist": "Watchlist",
    "nav.portfolio": "Portfolio",
    "nav.assets": "Assets",
    "nav.aiSettings": "AI settings",
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
    "detail.backToDashboard": "Back to dashboard",
    "quote.updatedAt": "Updated {time}",
    "quote.open": "Open",
    "quote.high": "High",
    "quote.low": "Low",
    "chart.noData": "No chart data is available.",
    "chart.title": "Price history",
    "chart.rangeSummary": "{range} range {min} - {max}",
    "chart.description": "Chart shows candlesticks for the selected range.",
    "chart.axisYTitle": "Price ({currency})",
    "chart.axisDate": "Date",
    "chart.candleClose": "Close",
    "chart.interactHint": "Hover or click the chart to inspect open, high, low, close, and volume for that time.",
    "rankings.title": "Market rankings",
    "rankings.description":
      "Free-only rankings. Taiwan shows daily institutional net buy/sell rankings, while U.S. rankings are quote-based over a limited tracked universe rather than the full market.",
    "rankings.market": "Market",
    "rankings.market.us": "U.S.",
    "rankings.market.tw": "Taiwan",
    "rankings.loading": "Loading rankings",
    "rankings.empty": "No ranking rows are available for this selection right now.",
    "rankings.tab.volume": "Volume",
    "rankings.tab.volumeUp": "Volume up",
    "rankings.tab.volumeDown": "Volume down",
    "rankings.tab.netBuy": "Net buy",
    "rankings.tab.netSell": "Net sell",
    "rankings.column.rank": "Rank",
    "rankings.column.symbol": "Symbol",
    "rankings.column.name": "Name",
    "rankings.column.lastPrice": "Last price",
    "rankings.column.changePercent": "Change %",
    "rankings.column.volume": "Volume",
    "rankings.column.volumeChangePercent": "Volume change %",
    "rankings.column.netBuySell": "Net buy / sell",
    "rankings.column.dataTime": "Data time",
    "rankings.column.source": "Source",
    "rankings.notice.usScope":
      "U.S. rankings here are quote-based and currently use a limited tracked universe rather than the full market.",
    "rankings.notice.twScope":
      "Taiwan rankings here use TWSE institutional daily data.",
    "rankings.notice.quoteUnofficial": "Free unofficial quote source; data may be delayed or unavailable.",
    "rankings.notice.twDaily": "Taiwan institutional net buy/sell rankings use daily data, not intraday real-time data.",
    "rankings.notice.twInstitutionalUnavailable":
      "Taiwan institutional daily data is temporarily unavailable from the free public source.",
    "rankings.freshness.quoteSnapshotOpen": "Market open; rankings use the latest quote snapshot.",
    "rankings.freshness.quoteSnapshotClosed": "Market closed; rankings use the latest available quote snapshot.",
    "rankings.freshness.volumeBaselineMissing":
      "Baseline unavailable on the first snapshot; volume change percent stays empty until a later refresh.",
    "rankings.freshness.volumeBaselineOpen": "Volume change uses the previous quote snapshot baseline.",
    "rankings.freshness.volumeBaselineClosed":
      "Market closed; volume change uses the previous quote snapshot baseline.",
    "rankings.freshness.twInstitutionalUnavailable":
      "Taiwan daily institutional ranking is unavailable from the free public source right now.",
    "rankings.freshness.twNetBuy":
      "Taiwan daily institutional ranking sorted by highest institutional net buy/sell shares.",
    "rankings.freshness.twNetSell":
      "Taiwan daily institutional ranking sorted by most negative institutional net buy/sell shares.",
    "rankings.freshness.twNetBuyClosed":
      "Market closed; Taiwan daily institutional ranking sorted by highest institutional net buy/sell shares.",
    "rankings.freshness.twNetSellClosed":
      "Market closed; Taiwan daily institutional ranking sorted by most negative institutional net buy/sell shares.",
    "rankings.source.quoteProviders": "Quote rankings from the app's free best-effort quote providers.",
    "rankings.source.twse": "TWSE T86 daily institutional data",
    "rankings.source.unsupported": "Unsupported in Phase 1 free-only rankings",
    "portfolio.title": "Portfolio",
    "portfolio.description":
      "Your actual positions (what you own). This list is separate from the dashboard watchlist, which is for research and tracking.",
    "portfolio.equity.title": "Holdings equity (USD & TWD)",
    "portfolio.count": "{count} positions",
    "portfolio.loading": "Loading portfolio quotes",
    "portfolio.loadError":
      "Could not load your holdings. Check that the backend is running and try again.",
    "portfolio.empty": "No positions yet. Search below to add symbols you hold.",
    "portfolio.table.currencyHint":
      "Last price and position value are shown in each holding's stock currency, such as USD or TWD.",
    "portfolio.search.eyebrow": "Holdings",
    "portfolio.search.title": "Add symbols to your portfolio",
    "portfolio.search.description": "Search U.S. and Taiwan stocks. Add only names you consider real holdings.",
    "portfolio.search.hint": "Results update as you type. Add or remove holdings without affecting your watchlist.",
    "portfolio.search.emptyPrompt": "Enter a symbol or company name to find stocks to add as holdings.",
    "portfolio.addHolding": "Add to portfolio",
    "portfolio.removeHolding": "Remove from portfolio",
    "portfolio.quantity.saving": "Saving...",
    "portfolio.quantity.saved": "Saved",
    "portfolio.assets.eyebrow": "Other assets",
    "portfolio.assets.title": "Consolidated assets",
    "portfolio.assets.count": "{count} assets",
    "portfolio.assets.loading": "Loading other assets",
    "portfolio.assets.empty": "No extra assets yet. Add cash, real estate, or other assets.",
    "portfolio.assets.add": "Add asset",
    "portfolio.assets.remove": "Remove asset",
    "portfolio.assets.column.type": "Type",
    "portfolio.assets.column.name": "Name",
    "portfolio.assets.column.currency": "Currency",
    "portfolio.assets.column.value": "Value",
    "portfolio.assets.type.cash": "Cash account",
    "portfolio.assets.type.realEstate": "Real estate",
    "portfolio.assets.type.other": "Other",
    "portfolio.aggregate.eyebrow": "Totals",
    "portfolio.aggregate.title": "Aggregated assets by currency",
    "portfolio.aggregate.combinedUsd": "Combined total (USD)",
    "portfolio.aggregate.combinedTwd": "Combined total (TWD)",
    "portfolio.aggregate.liquidAssets": "Liquid assets excl. real estate",
    "portfolio.aggregate.fxRate": "FX rate",
    "portfolio.history.loading": "Loading historical portfolio values",
    "portfolio.history.empty": "No historical data yet. Hourly snapshots appear while Taiwan or U.S. markets are open.",
    "portfolio.history.range": "Range: {range}",
    "portfolio.history.zoomIn": "Zoom in",
    "portfolio.history.zoomOut": "Zoom out",
    "portfolio.history.interactHint": "Hover or click to inspect the hourly snapshot, market session, and FX rate used.",
    "portfolio.history.value": "Value",
    "portfolio.history.session": "Session",
    "portfolio.history.session.tw": "Taiwan market",
    "portfolio.history.session.us": "U.S. market",
    "portfolio.history.holdingsUsd": "Holdings value history (USD)",
    "portfolio.history.holdingsTwd": "Holdings value history (TWD)",
    "portfolio.history.combinedAssetsUsd": "Combined assets history (USD)",
    "portfolio.history.combinedAssetsTwd": "Combined assets history (TWD)",
    "aiSettings.title": "AI API key settings",
    "aiSettings.description": "Manage OpenAI and Perplexity API keys for stock detail AI output.",
    "twInsights.title": "Taiwan fundamentals snapshot",
    "twInsights.description":
      "Additional Taiwan-only context similar to local investor portals: revenue trend, peer momentum, and dividends.",
    "twInsights.loading": "Loading Taiwan insights",
    "twInsights.revenue.title": "Monthly revenue trend (K TWD)",
    "twInsights.revenue.latest": "Latest",
    "twInsights.peer.title": "Peer performance snapshot",
    "twInsights.peer.period": "Period",
    "twInsights.peer.company": "Company",
    "twInsights.peer.industry": "Industry avg",
    "twInsights.peer.rank": "Rank",
    "twInsights.dividend.title": "Dividend history",
    "twInsights.dividend.avg2y": "Avg cash yield (2Y)",
    "twInsights.dividend.year": "Year",
    "twInsights.dividend.cash": "Cash",
    "twInsights.dividend.stock": "Stock",
    "twInsights.dividend.yield": "Cash yield",
    "twInsights.dividend.exDate": "Ex-dividend",
    "twInsights.dividend.payDate": "Pay date",
    "twInsights.dividend.distributionTitle": "Historical dividend distribution",
    "twInsights.dividend.policyTitle": "Dividend policy detail",
    "twInsights.dividend.byPayoutYear": "By payout year",
    "twInsights.dividend.byEarningsYear": "By earnings year",
    "twInsights.dividend.earningsYear": "Earnings year",
    "portfolio.column.symbol": "Symbol",
    "portfolio.column.name": "Name",
    "portfolio.column.market": "Market",
    "portfolio.column.quantity": "Shares",
    "portfolio.column.last": "Last price (stock currency)",
    "portfolio.column.marketValue": "Position value (stock currency)",
    "portfolio.column.change": "Change",
    "portfolio.column.changePercent": "Change %",
    "portfolio.column.volume": "Volume",
    "portfolio.column.updated": "Updated",
    "portfolio.column.actions": "Actions",
    "keyStats.title": "Key statistics",
    "keyStats.open": "Open",
    "keyStats.previousClose": "Previous close",
    "keyStats.volume": "Volume",
    "keyStats.avgVolume10d": "Average volume (10 day)",
    "keyStats.marketCap": "Market cap",
    "keyStats.dayRange": "Day range",
    "keyStats.week52Range": "52-week range",
    "keyStats.low": "Low",
    "keyStats.high": "High",
    "keyStats.sharesOutstanding": "Shares outstanding",
    "keyStats.beta": "Beta",
    "keyStats.peTtm": "P/E (TTM)",
    "keyStats.epsTtm": "EPS (TTM)",
    "keyStats.dividendYield": "Quarterly dividend / yield",
    "keyStats.exDividendDate": "Ex-dividend date",
    "keyStats.dividendPayDate": "Dividend payable date",
    "keyStats.nextEarnings": "Next earnings date",
    "keyStats.na": "—",
    "keyStats.infoHint": "Definition may vary by data provider.",
    "keyStats.fundamentalsUnavailable":
      "Market cap, beta, P/E, EPS, and dividend/earnings dates are not included in our free price feeds. Yahoo’s detailed fundamentals API requires browser cookies, so those fields stay empty unless you configure an API key (e.g. set FINNHUB_API_KEY on the backend for Finnhub) or connect another fundamentals provider.",
  },
  "zh-TW": {
    "nav.dashboard": "儀表板",
    "nav.markets": "市場",
    "nav.rankings": "排行",
    "nav.watchlist": "自選",
    "nav.portfolio": "投資組合",
    "nav.assets": "資產總覽",
    "nav.aiSettings": "AI 設定",
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
    "detail.backToDashboard": "返回儀表板",
    "quote.updatedAt": "更新時間 {time}",
    "quote.open": "開盤",
    "quote.high": "最高",
    "quote.low": "最低",
    "chart.noData": "沒有可顯示的圖表資料。",
    "chart.title": "價格走勢",
    "chart.rangeSummary": "{range} 區間高低 {min} - {max}",
    "chart.description": "圖表以 K 線顯示所選區間的價格走勢。",
    "chart.axisYTitle": "價格（{currency}）",
    "chart.axisDate": "日期",
    "chart.candleClose": "收盤",
    "chart.interactHint": "將游標移到圖表上或點選，可檢視該時間點的開高低收與成交量。",
    "rankings.title": "市場排行",
    "rankings.description":
      "此頁顯示免費資料可支援的排行：台股顯示法人日排行；美股顯示有限追蹤清單的報價排行，並非全市場排行。",
    "rankings.market": "市場",
    "rankings.market.us": "美股",
    "rankings.market.tw": "台股",
    "rankings.loading": "載入排行中",
    "rankings.empty": "目前沒有可顯示的排行資料。",
    "rankings.tab.volume": "成交量",
    "rankings.tab.volumeUp": "量增",
    "rankings.tab.volumeDown": "量減",
    "rankings.tab.netBuy": "買超",
    "rankings.tab.netSell": "賣超",
    "rankings.column.rank": "排名",
    "rankings.column.symbol": "代碼",
    "rankings.column.name": "名稱",
    "rankings.column.lastPrice": "最新價",
    "rankings.column.changePercent": "漲跌幅",
    "rankings.column.volume": "成交量",
    "rankings.column.volumeChangePercent": "量變化 %",
    "rankings.column.netBuySell": "買賣超",
    "rankings.column.dataTime": "資料時間",
    "rankings.column.source": "來源",
    "rankings.notice.usScope": "此處的美股排行以報價資料為主，且目前僅涵蓋有限追蹤清單，並非全市場排行。",
    "rankings.notice.twScope": "此處的台股排行使用 TWSE 法人日資料。",
    "rankings.notice.quoteUnofficial": "免費非官方報價來源可能延遲，或暫時無法提供資料。",
    "rankings.notice.twDaily": "台股三大法人買賣超為日資料，非盤中即時資料。",
    "rankings.notice.twInstitutionalUnavailable": "目前無法從免費公開來源取得台股法人日資料。",
    "rankings.freshness.quoteSnapshotOpen": "市場開盤中；排行使用最新報價快照。",
    "rankings.freshness.quoteSnapshotClosed": "市場休市；排行使用最新可得的報價快照。",
    "rankings.freshness.volumeBaselineMissing": "首次快照尚無基準值；量變化百分比會在後續刷新後顯示。",
    "rankings.freshness.volumeBaselineOpen": "量變化使用前一次報價快照作為基準。",
    "rankings.freshness.volumeBaselineClosed": "市場休市；量變化使用前一次報價快照作為基準。",
    "rankings.freshness.twInstitutionalUnavailable": "目前無法從免費公開來源取得台股法人日排行。",
    "rankings.freshness.twNetBuy": "台股法人日排行依三大法人買賣超股數由高到低排序。",
    "rankings.freshness.twNetSell": "台股法人日排行依三大法人買賣超股數由低到高排序。",
    "rankings.freshness.twNetBuyClosed": "市場休市；台股法人日排行依三大法人買賣超股數由高到低排序。",
    "rankings.freshness.twNetSellClosed": "市場休市；台股法人日排行依三大法人買賣超股數由低到高排序。",
    "rankings.source.quoteProviders": "排行使用本應用程式的免費最佳努力報價來源。",
    "rankings.source.twse": "TWSE T86 法人日資料",
    "rankings.source.unsupported": "Phase 1 免費排行尚不支援",
    "portfolio.title": "投資組合",
    "portfolio.description":
      "此處為您實際持有的標的，與儀表板的「自選清單」分開；自選清單適合研究與追蹤。",
    "portfolio.equity.title": "持股市值（美金與台幣）",
    "portfolio.count": "{count} 檔持倉",
    "portfolio.loading": "載入投資組合報價中",
    "portfolio.loadError": "無法載入持倉。請確認後端已啟動後再試。",
    "portfolio.empty": "尚無持倉。請在下方搜尋並加入您持有的標的。",
    "portfolio.table.currencyHint": "最新價與持倉市值皆依各檔股票原幣顯示，例如 USD 或 TWD。",
    "portfolio.search.eyebrow": "持倉",
    "portfolio.search.title": "將標的加入投資組合",
    "portfolio.search.description": "搜尋美股與台股，僅加入您視為實際持倉的標的。",
    "portfolio.search.hint": "輸入後即時更新。加入或移除持倉不會影響自選清單。",
    "portfolio.search.emptyPrompt": "輸入代碼或公司名稱，找出要加入為持倉的標的。",
    "portfolio.addHolding": "加入投資組合",
    "portfolio.removeHolding": "從投資組合移除",
    "portfolio.quantity.saving": "儲存中...",
    "portfolio.quantity.saved": "已儲存",
    "portfolio.assets.eyebrow": "其他資產",
    "portfolio.assets.title": "整體資產",
    "portfolio.assets.count": "{count} 筆資產",
    "portfolio.assets.loading": "載入其他資產中",
    "portfolio.assets.empty": "尚無其他資產。可新增現金、房地產或其他資產。",
    "portfolio.assets.add": "新增資產",
    "portfolio.assets.remove": "移除資產",
    "portfolio.assets.column.type": "類型",
    "portfolio.assets.column.name": "名稱",
    "portfolio.assets.column.currency": "幣別",
    "portfolio.assets.column.value": "金額",
    "portfolio.assets.type.cash": "現金帳戶",
    "portfolio.assets.type.realEstate": "房地產",
    "portfolio.assets.type.other": "其他",
    "portfolio.aggregate.eyebrow": "資產總覽",
    "portfolio.aggregate.title": "依幣別彙總資產",
    "portfolio.aggregate.combinedUsd": "合併總資產（USD）",
    "portfolio.aggregate.combinedTwd": "合併總資產（TWD）",
    "portfolio.aggregate.liquidAssets": "可變現資產（不含房地產）",
    "portfolio.aggregate.fxRate": "匯率",
    "portfolio.history.loading": "載入歷史資產資料中",
    "portfolio.history.empty": "目前還沒有歷史資料。台灣或美國市場開盤期間會按小時累積快照。",
    "portfolio.history.range": "區間：{range}",
    "portfolio.history.zoomIn": "放大",
    "portfolio.history.zoomOut": "縮小",
    "portfolio.history.interactHint": "將游標移到圖表上或點選，可檢視每小時快照、市場時段與換匯匯率。",
    "portfolio.history.value": "數值",
    "portfolio.history.session": "市場時段",
    "portfolio.history.session.tw": "台灣市場",
    "portfolio.history.session.us": "美國市場",
    "portfolio.history.holdingsUsd": "持股市值歷史（USD）",
    "portfolio.history.holdingsTwd": "持股市值歷史（TWD）",
    "portfolio.history.combinedAssetsUsd": "合併總資產歷史（USD）",
    "portfolio.history.combinedAssetsTwd": "合併總資產歷史（TWD）",
    "aiSettings.title": "AI API 金鑰設定",
    "aiSettings.description": "管理 OpenAI 與 Perplexity API 金鑰，供個股明細 AI 輸出使用。",
    "twInsights.title": "台股基本面快照",
    "twInsights.description": "僅台股顯示：月營收趨勢、同業表現與股利資訊。",
    "twInsights.loading": "載入台股補充資訊中",
    "twInsights.revenue.title": "月營收趨勢（仟元）",
    "twInsights.revenue.latest": "最新",
    "twInsights.peer.title": "同業表現快照",
    "twInsights.peer.period": "期間",
    "twInsights.peer.company": "公司",
    "twInsights.peer.industry": "同業平均",
    "twInsights.peer.rank": "排名",
    "twInsights.dividend.title": "歷年股利",
    "twInsights.dividend.avg2y": "近2年平均現金殖利率",
    "twInsights.dividend.year": "年度",
    "twInsights.dividend.cash": "現金股利",
    "twInsights.dividend.stock": "股票股利",
    "twInsights.dividend.yield": "現金殖利率",
    "twInsights.dividend.exDate": "除息日",
    "twInsights.dividend.payDate": "發放日",
    "twInsights.dividend.distributionTitle": "歷年股利分配",
    "twInsights.dividend.policyTitle": "歷年股利政策",
    "twInsights.dividend.byPayoutYear": "股利發放期間",
    "twInsights.dividend.byEarningsYear": "股利所屬期間",
    "twInsights.dividend.earningsYear": "所屬期間",
    "portfolio.column.symbol": "代碼",
    "portfolio.column.name": "名稱",
    "portfolio.column.market": "市場",
    "portfolio.column.quantity": "股數",
    "portfolio.column.last": "最新價（原幣）",
    "portfolio.column.marketValue": "持倉市值（原幣）",
    "portfolio.column.change": "漲跌",
    "portfolio.column.changePercent": "漲跌幅",
    "portfolio.column.volume": "成交量",
    "portfolio.column.updated": "更新時間",
    "portfolio.column.actions": "操作",
    "keyStats.title": "關鍵數據",
    "keyStats.open": "開盤",
    "keyStats.previousClose": "前收",
    "keyStats.volume": "成交量",
    "keyStats.avgVolume10d": "10 日均量",
    "keyStats.marketCap": "市值",
    "keyStats.dayRange": "當日區間",
    "keyStats.week52Range": "52 週區間",
    "keyStats.low": "最低",
    "keyStats.high": "最高",
    "keyStats.sharesOutstanding": "流通股數",
    "keyStats.beta": "Beta",
    "keyStats.peTtm": "本益比（TTM）",
    "keyStats.epsTtm": "每股盈餘（TTM）",
    "keyStats.dividendYield": "季配息 / 殖利率",
    "keyStats.exDividendDate": "除息日",
    "keyStats.dividendPayDate": "配息發放日",
    "keyStats.nextEarnings": "下次財報日",
    "keyStats.na": "—",
    "keyStats.infoHint": "定義可能因資料來源而異。",
    "keyStats.fundamentalsUnavailable":
      "市值、Beta、本益比、每股盈餘，以及除息／配息／財報日期等，免費即時報價來源通常不提供；Yahoo 詳細基本面 API 亦需要瀏覽器 Cookie，因此預設會顯示「—」。若要在後端補齊（例如設定 FINNHUB_API_KEY 使用 Finnhub），或改接其他基本面資料商，即可顯示這些欄位。",
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
