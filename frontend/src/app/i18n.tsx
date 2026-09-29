"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Stock } from "@shared/stock";
import {
  portfolioMarketSectionMessages,
  type PortfolioMarketSectionTranslationKey,
} from "../views/portfolio/portfolioMarketSectionI18n";
import {
  realizedGainsMessages,
  type RealizedGainsTranslationKey,
} from "../views/realized-gains/realizedGainsI18n";

export type AppLanguage = "en" | "zh-TW";

export type TranslationKey =
  | "nav.dashboard"
  | "nav.alerts"
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
  | "marketSnapshot.title"
  | "marketSnapshot.eyebrow"
  | "marketSnapshot.fxLabel"
  | "marketSnapshot.oilLabel"
  | "marketSnapshot.perBarrel"
  | "marketSnapshot.updated"
  | "marketSnapshot.loading"
  | "marketSnapshot.source"
  | "marketSnapshot.twdUnit"
  | "marketSnapshot.stale"
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
  | "watchlist.manageAlerts"
  | "watchlist.market.tw"
  | "watchlist.market.us"
  | "watchlist.market.unknown"
  | "watchlist.sectionCount"
  | "watchlist.sectionCount.one"
  | "alerts.title"
  | "alerts.description"
  | "alerts.form.eyebrow"
  | "alerts.form.createTitle"
  | "alerts.form.editTitle"
  | "alerts.form.stockId"
  | "alerts.form.stockIdPlaceholder"
  | "alerts.form.stockIdHelper"
  | "alerts.form.ruleType"
  | "alerts.form.threshold"
  | "alerts.form.enabled"
  | "alerts.form.submitCreate"
  | "alerts.form.submitUpdate"
  | "alerts.form.cancelEdit"
  | "alerts.error.unknownStock"
  | "alerts.error.unknownStockId"
  | "alerts.error.ambiguousStock"
  | "alerts.rules.title"
  | "alerts.rules.empty"
  | "alerts.events.title"
  | "alerts.events.empty"
  | "alerts.events.clearHistory"
  | "alerts.events.clearConfirmTitle"
  | "alerts.events.clearConfirmMessage"
  | "alerts.events.clearConfirmCancel"
  | "alerts.events.clearConfirmAction"
  | "alerts.ruleType.priceAbove"
  | "alerts.ruleType.priceBelow"
  | "alerts.status.enabled"
  | "alerts.status.disabled"
  | "alerts.status.conditionMet"
  | "alerts.status.monitoring"
  | "alerts.actions.edit"
  | "alerts.actions.pause"
  | "alerts.actions.resume"
  | "alerts.actions.delete"
  | "alerts.column.stock"
  | "alerts.column.rule"
  | "alerts.column.threshold"
  | "alerts.column.status"
  | "alerts.column.lastObserved"
  | "alerts.column.lastTriggered"
  | "alerts.column.actions"
  | "alerts.column.observedPrice"
  | "alerts.column.triggeredAt"
  | "alerts.column.message"
  | RealizedGainsTranslationKey
  | "stock.market"
  | "stock.updatedAt"
  | "stock.prevClose"
  | "stock.volume"
  | "stock.viewDetails"
  | "stock.remove"
  | "stock.add"
  | "stock.quoteLoadError"
  | "stock.quoteLoading"
  | "stock.quoteRefreshing"
  | "stock.quoteUnavailableTitle"
  | "stock.quoteUnavailableBody"
  | "stock.quoteRetry"
  | "stock.weekRange"
  | "stock.weekRangeNoData"
  | "detail.invalidStockId"
  | "detail.loading"
  | "detail.selectHistoryRange"
  | "detail.backToDashboard"
  | "detail.createAlert"
  | "detail.twSignals.eyebrow"
  | "detail.twSignals.title"
  | "detail.twSignals.description"
  | "detail.twSignals.institutionalTitle"
  | "detail.twSignals.marginShortTitle"
  | "detail.twSignals.ownershipTitle"
  | "detail.twSignals.profitMarginsTitle"
  | "quote.updatedAt"
  | "quote.open"
  | "quote.high"
  | "quote.low"
  | "quote.limitUp"
  | "quote.limitDown"
  | "chart.noData"
  | "chart.title"
  | "chart.rangeSummary"
  | "chart.description"
  | "chart.axisYTitle"
  | "chart.axisDate"
  | "chart.candleClose"
  | "chart.interactHint"
  | "chart.sma10"
  | "chart.sma20"
  | "chart.sma60"
  | "chart.smaLegend"
  | "chart.smaDirectionUp"
  | "chart.smaDirectionDown"
  | "chart.smaDirectionFlat"
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
  | "rankings.tab.changeUp"
  | "rankings.tab.changeDown"
  | "rankings.tab.lowPe"
  | "rankings.tab.netBuy"
  | "rankings.tab.netSell"
  | "rankings.tab.limitUp"
  | "rankings.tab.limitDown"
  | "rankings.empty.limitUp"
  | "rankings.empty.limitDown"
  | "rankings.liveOnly"
  | "rankings.column.rank"
  | "rankings.column.symbol"
  | "rankings.column.board"
  | "rankings.column.name"
  | "rankings.column.lastPrice"
  | "rankings.column.changePercent"
  | "rankings.column.volume"
  | "rankings.column.limitUpPrice"
  | "rankings.column.limitDownPrice"
  | "rankings.column.priceLimitStatus"
  | "rankings.column.netBuySell"
  | "rankings.column.peRatio"
  | "rankings.column.peRatioSource"
  | "rankings.column.valuationAsOf"
  | "rankings.column.dataTime"
  | "rankings.column.source"
  | "rankings.meta.snapshotTimestamp"
  | "rankings.meta.source"
  | "rankings.meta.sourceType"
  | "rankings.sourceType.freePublic"
  | "rankings.sourceType.freeUnofficial"
  | "rankings.sourceType.cache"
  | "rankings.sourceType.unsupported"
  | "rankings.snapshotState.intraday"
  | "rankings.snapshotState.liveOnly"
  | "rankings.snapshotState.daily"
  | "rankings.snapshotState.eodArchive"
  | "rankings.snapshotState.cache"
  | "rankings.snapshotState.unsupported"
  | "rankings.notice.usScopeYahoo"
  | "rankings.notice.usScopeSp500"
  | "rankings.notice.usScopeQuote"
  | "rankings.notice.usScopeDjia"
  | "rankings.notice.usScopeNasdaq100"
  | "rankings.notice.usScopePending"
  | "rankings.notice.twInstScopeTwse"
  | "rankings.notice.twInstScopeTpex"
  | "rankings.notice.twInstScopeTpexUnavailable"
  | "rankings.notice.twQuoteScopeTwse"
  | "rankings.notice.twQuoteScopeTpex"
  | "rankings.notice.twQuoteScopeYahooTwse"
  | "rankings.notice.twQuoteScopeYahooTpex"
  | "rankings.notice.twQuoteScopeFallbackTwse"
  | "rankings.notice.twQuoteScopeFallbackTpex"
  | "rankings.twBoard.groupLabel"
  | "rankings.twBoard.all"
  | "rankings.twBoard.twse"
  | "rankings.twBoard.tpex"
  | "rankings.usUniverse.groupLabel"
  | "rankings.usUniverse.djia"
  | "rankings.usUniverse.nasdaq100"
  | "rankings.refresh.action"
  | "rankings.refresh.loading"
  | "rankings.refresh.failed"
  | "rankings.date.label"
  | "rankings.date.current"
  | "rankings.date.historicalBanner"
  | "rankings.date.loadHistory"
  | "rankings.date.historyUnavailable"
  | "rankings.date.historyWaking"
  | "rankings.notice.quoteUnofficial"
  | "rankings.notice.twDaily"
  | "rankings.notice.twDailyTpex"
  | "rankings.notice.usInstitutionalUnsupported"
  | "rankings.notice.usPriceLimitUnsupported"
  | "rankings.notice.twPriceLimit"
  | "rankings.notice.twPriceLimitPartial"
  | "rankings.notice.twPriceLimitUnavailable"
  | "rankings.freshness.twPriceLimitOpen"
  | "rankings.freshness.twPriceLimitClosed"
  | "rankings.freshness.unsupported"
  | "rankings.source.twPriceLimit"
  | "rankings.badge.limitUp"
  | "rankings.badge.limitDown"
  | "rankings.notice.twInstitutionalUnavailable"
  | "rankings.notice.twInstitutionalTpexUnavailable"
  | "rankings.notice.twQuoteFallback"
  | "rankings.freshness.closedLatestSnapshot"
  | "rankings.freshness.cachedCurrentBucket"
  | "rankings.freshness.cachedTwDaily"
  | "rankings.freshness.freeSourceFallback"
  | "rankings.freshness.noCachedSnapshot"
  | "rankings.freshness.quoteSnapshotOpen"
  | "rankings.freshness.quoteSnapshotClosed"
  | "rankings.freshness.changeSnapshotOpen"
  | "rankings.freshness.changeSnapshotClosed"
  | "rankings.freshness.yahooVolumeOpen"
  | "rankings.freshness.yahooVolumeClosed"
  | "rankings.freshness.yahooChangeOpen"
  | "rankings.freshness.yahooChangeClosed"
  | "rankings.freshness.sp500MoversOpen"
  | "rankings.freshness.sp500MoversClosed"
  | "rankings.freshness.valuationLowPeOpen"
  | "rankings.freshness.valuationLowPeClosed"
  | "rankings.freshness.volumeBaselineMissing"
  | "rankings.freshness.volumeBaselineOpen"
  | "rankings.freshness.volumeBaselineClosed"
  | "rankings.freshness.twInstitutionalUnavailable"
  | "rankings.freshness.twInstitutionalTpexUnavailable"
  | "rankings.freshness.twNetBuy"
  | "rankings.freshness.twNetSell"
  | "rankings.freshness.twNetBuyClosed"
  | "rankings.freshness.twNetSellClosed"
  | "rankings.freshness.tpexNetBuy"
  | "rankings.freshness.tpexNetSell"
  | "rankings.freshness.tpexNetBuyClosed"
  | "rankings.freshness.tpexNetSellClosed"
  | "rankings.freshness.twQuoteFallbackOpen"
  | "rankings.freshness.twQuoteFallbackClosed"
  | "rankings.source.quoteProviders"
  | "rankings.source.djiaQuote"
  | "rankings.source.nasdaq100Quote"
  | "rankings.source.yahooPreRanked"
  | "rankings.source.sp500Movers"
  | "rankings.source.valuationUniverse"
  | "rankings.source.twQuoteFallback"
  | "rankings.source.tpexInstitutional"
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
  | PortfolioMarketSectionTranslationKey
  | "portfolio.column.symbol"
  | "portfolio.column.name"
  | "portfolio.column.market"
  | "portfolio.column.quantity"
  | "portfolio.column.averageCost"
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
  | "portfolio.ai.eyebrow"
  | "portfolio.ai.title"
  | "portfolio.ai.description"
  | "portfolio.ai.analyze"
  | "portfolio.ai.reanalyze"
  | "portfolio.ai.settings"
  | "portfolio.ai.loading"
  | "portfolio.ai.notConfigured"
  | "portfolio.ai.emptyPortfolioError"
  | "portfolio.ai.noAnalyzableHoldings"
  | "portfolio.ai.failed"
  | "portfolio.ai.empty"
  | "portfolio.ai.totalMarketValueUsd"
  | "portfolio.ai.totalMarketValueTwd"
  | "portfolio.ai.totalCostBasisUsd"
  | "portfolio.ai.totalUnrealizedPlUsd"
  | "portfolio.ai.totalUnrealizedPlPercent"
  | "portfolio.ai.concentration"
  | "portfolio.ai.topHolding"
  | "portfolio.ai.top3"
  | "portfolio.ai.level"
  | "portfolio.ai.level.low"
  | "portfolio.ai.level.moderate"
  | "portfolio.ai.level.high"
  | "portfolio.ai.exposure"
  | "portfolio.ai.largestHoldings"
  | "portfolio.ai.report"
  | "portfolio.ai.noReport"
  | "portfolio.ai.noReportProviderError"
  | "portfolio.ai.noReportSeeWarnings"
  | "portfolio.ai.warningsHeading"
  | "portfolio.ai.skipped"
  | "portfolio.ai.symbol"
  | "portfolio.ai.weight"
  | "portfolio.ai.marketValue"
  | "portfolio.ai.unrealizedPl"
  | "stock.ai.eyebrow"
  | "stock.ai.title"
  | "stock.ai.description"
  | "stock.ai.refresh"
  | "stock.ai.loading"
  | "stock.ai.refreshing"
  | "stock.ai.refreshFailed"
  | "stock.ai.loadFailed"
  | "stock.ai.explanation"
  | "stock.ai.noCache"
  | "stock.ai.sources"
  | "stock.ai.analyzedAt"
  | "stock.ai.disclaimer"
  | "stock.ai.notice.historyFetchFailed"
  | "stock.ai.notice.noHistory"
  | "stock.ai.notice.unusableHistory"
  | "stock.ai.notice.metadataUnavailable"
  | "stock.ai.notice.invalidTicker"
  | "stock.ai.notice.aiSkippedNoHistory"
  | "stock.ai.notice.newsUnavailable"
  | "stock.ai.metric.ticker"
  | "stock.ai.metric.company"
  | "stock.ai.metric.currency"
  | "stock.ai.metric.sector"
  | "stock.ai.metric.industry"
  | "stock.ai.metric.marketCap"
  | "stock.ai.metric.trailingPe"
  | "stock.ai.metric.forwardPe"
  | "stock.ai.metric.currentPrice"
  | "stock.ai.metric.return1m"
  | "stock.ai.metric.return3m"
  | "stock.ai.metric.return6m"
  | "stock.ai.metric.return1y"
  | "stock.ai.metric.sma50"
  | "stock.ai.metric.sma200"
  | "stock.ai.metric.priceVsSma50"
  | "stock.ai.metric.priceVsSma200"
  | "stock.ai.metric.rsi14"
  | "stock.ai.metric.volatility30d"
  | "stock.ai.metric.maxDrawdown1y"
  | "stock.ai.metric.averageVolume30d"
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
  | "portfolio.aggregate.totalCostBasis"
  | "portfolio.aggregate.totalGain"
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
  | "aiSettings.providersEyebrow"
  | "aiSettings.providersIntro"
  | "aiSettings.activeProviderTitle"
  | "aiSettings.activeProviderHelp"
  | "aiSettings.activeProviderLabel"
  | "aiSettings.activeProviderCurrent"
  | "aiSettings.activeProviderAuto"
  | "aiSettings.saveActiveProvider"
  | "aiSettings.apiKeysTitle"
  | "aiSettings.keyProviderLabel"
  | "aiSettings.keyConfigured"
  | "aiSettings.keyNotConfigured"
  | "aiSettings.openAiStatus"
  | "aiSettings.perplexityStatus"
  | "aiSettings.notConfiguredShort"
  | "aiSettings.apiKeyLabel"
  | "aiSettings.apiKeyPlaceholder"
  | "aiSettings.saveApiKey"
  | "aiSettings.checking"
  | "aiSettings.saving"
  | "twInsights.title"
  | "twInsights.description"
  | "twInsights.loading"
  | "twInsights.unavailable"
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

type TwQuoteFallbackTranslationKey =
  | "rankings.notice.twQuoteScopeYahooTwse"
  | "rankings.notice.twQuoteScopeYahooTpex"
  | "rankings.notice.twQuoteScopeFallbackTwse"
  | "rankings.notice.twQuoteScopeFallbackTpex"
  | "rankings.notice.twQuoteFallback"
  | "rankings.freshness.twQuoteFallbackOpen"
  | "rankings.freshness.twQuoteFallbackClosed"
  | "rankings.source.twQuoteFallback";

function twQuoteFallbackMessages(language: AppLanguage): Record<TwQuoteFallbackTranslationKey, string> {
  const zh = language === "zh-TW";
  return {
    "rankings.notice.twQuoteScopeYahooTwse": zh
      ? "此分頁的台股報價排行來自 Yahoo Finance 較廣的上市（TWSE）範圍，不是本應用程式的小型設定清單。"
      : "Taiwan quote rankings for this tab come from Yahoo Finance's broader TWSE (listed) universe, not the app's small configured quote list.",
    "rankings.notice.twQuoteScopeYahooTpex": zh
      ? "此分頁的台股報價排行來自 Yahoo Finance 較廣的上櫃（TPEx）範圍，不是本應用程式的小型設定清單。"
      : "Taiwan quote rankings for this tab come from Yahoo Finance's broader TPEx (OTC) universe, not the app's small configured quote list.",
    "rankings.notice.twQuoteScopeFallbackTwse": zh
      ? "目前無法取得較廣的台股報價排行。此上市（TWSE）表格使用小型內建備援清單，不是完整的上市排行。"
      : "Broader Taiwan quote rankings are unavailable. This TWSE table uses a small hard-coded fallback universe, not a complete listed-board ranking.",
    "rankings.notice.twQuoteScopeFallbackTpex": zh
      ? "目前無法取得較廣的台股報價排行。此上櫃（TPEx）表格使用小型內建備援清單，不是完整的上櫃排行。"
      : "Broader Taiwan quote rankings are unavailable. This TPEx table uses a small hard-coded fallback universe, not a complete OTC-board ranking.",
    "rankings.notice.twQuoteFallback": zh
      ? "目前無法取得較廣的台股報價排行。此表格使用小型內建備援清單，不是完整的上市／上櫃排行。"
      : "Broader Taiwan quote rankings are temporarily unavailable. This table uses a small hard-coded fallback universe, not a complete TWSE/TPEx ranking.",
    "rankings.freshness.twQuoteFallbackOpen": zh
      ? "市場開盤中；因較廣的台股排行來源無法使用，改顯示小型內建報價備援清單。"
      : "Market open; showing a limited hard-coded quote fallback because the broader Taiwan ranking source is unavailable.",
    "rankings.freshness.twQuoteFallbackClosed": zh
      ? "市場休市；因較廣的台股排行來源無法使用，改顯示小型內建報價備援清單。"
      : "Market closed; showing a limited hard-coded quote fallback because the broader Taiwan ranking source is unavailable.",
    "rankings.source.twQuoteFallback": zh
      ? "有限的上市／上櫃內建報價備援清單；不是完整市場排行。"
      : "Limited hard-coded TWSE/TPEx quote fallback; not a full-board ranking.",
  };
}

const messages: Record<AppLanguage, Record<TranslationKey, string>> = {
  en: {
    "nav.dashboard": "Dashboard",
    "nav.alerts": "Alerts",
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
    "marketSnapshot.title": "Market Snapshot",
    "marketSnapshot.eyebrow": "Key figures",
    "marketSnapshot.fxLabel": "USD/TWD Exchange Rate",
    "marketSnapshot.oilLabel": "WTI Crude Oil",
    "marketSnapshot.perBarrel": "/ barrel",
    "marketSnapshot.updated": "Updated",
    "marketSnapshot.loading": "Loading market snapshot",
    "marketSnapshot.source": "Source",
    "marketSnapshot.twdUnit": "TWD",
    "marketSnapshot.stale": "Stale",
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
    "watchlist.manageAlerts": "Manage alerts",
    "watchlist.market.tw": "Taiwan Stocks",
    "watchlist.market.us": "U.S. Stocks",
    "watchlist.market.unknown": "Other symbols",
    "watchlist.sectionCount": "{count} symbols",
    "watchlist.sectionCount.one": "{count} symbol",
    "alerts.title": "Threshold alerts",
    "alerts.description":
      "Create price-above and price-below rules for stocks you care about, then review the recent trigger history in one place.",
    "alerts.form.eyebrow": "Rule editor",
    "alerts.form.createTitle": "Create alert rule",
    "alerts.form.editTitle": "Edit alert rule",
    "alerts.form.stockId": "Stock symbol",
    "alerts.form.stockIdPlaceholder": "For example: AAPL, 2330, or US:AAPL",
    "alerts.form.stockIdHelper": "Enter a ticker like AAPL / 2330, or a canonical ID like US:AAPL.",
    "alerts.form.ruleType": "Rule type",
    "alerts.form.threshold": "Threshold price",
    "alerts.form.enabled": "Rule status",
    "alerts.form.submitCreate": "Create alert",
    "alerts.form.submitUpdate": "Save changes",
    "alerts.form.cancelEdit": "Cancel",
    "alerts.error.unknownStock": "Stock not found. Enter a ticker or choose a canonical ID such as US:AAPL.",
    "alerts.error.unknownStockId":
      "Stock not found: {stockId}. Enter a ticker or choose a canonical ID such as US:AAPL.",
    "alerts.error.ambiguousStock":
      'Ambiguous symbol "{query}". Choose one of: {choices}',
    "alerts.rules.title": "Current rules",
    "alerts.rules.empty": "No alert rules yet. Create one above to start tracking threshold events.",
    "alerts.events.title": "Recent events",
    "alerts.events.empty": "No alert events yet. Events appear here when a rule crosses its threshold.",
    "alerts.events.clearHistory": "Clear history",
    "alerts.events.clearConfirmTitle": "Clear alert event history",
    "alerts.events.clearConfirmMessage":
      "Clear all alert event history? This will not delete your alert rules.",
    "alerts.events.clearConfirmCancel": "Cancel",
    "alerts.events.clearConfirmAction": "Clear history",
    "alerts.ruleType.priceAbove": "Price above",
    "alerts.ruleType.priceBelow": "Price below",
    "alerts.status.enabled": "Enabled",
    "alerts.status.disabled": "Disabled",
    "alerts.status.conditionMet": "Triggered state",
    "alerts.status.monitoring": "Monitoring",
    "alerts.actions.edit": "Edit",
    "alerts.actions.pause": "Pause",
    "alerts.actions.resume": "Resume",
    "alerts.actions.delete": "Delete",
    "alerts.column.stock": "Stock",
    "alerts.column.rule": "Rule",
    "alerts.column.threshold": "Threshold",
    "alerts.column.status": "Status",
    "alerts.column.lastObserved": "Last observed",
    "alerts.column.lastTriggered": "Last triggered",
    "alerts.column.actions": "Actions",
    "alerts.column.observedPrice": "Observed price",
    "alerts.column.triggeredAt": "Triggered",
    "alerts.column.message": "Message",
    ...realizedGainsMessages("en"),
    "stock.market": "Market",
    "stock.updatedAt": "Updated",
    "stock.prevClose": "Prev close",
    "stock.volume": "Volume",
    "stock.viewDetails": "View details",
    "stock.remove": "Remove",
    "stock.add": "Add",
    "stock.quoteLoadError": "Unable to load quote right now.",
    "stock.quoteLoading": "Loading quote",
    "stock.quoteRefreshing": "Refreshing quote",
    "stock.quoteUnavailableTitle": "Unable to load quote",
    "stock.quoteUnavailableBody": "The external data source is temporarily unavailable. Please try again later.",
    "stock.quoteRetry": "Refresh",
    "stock.weekRange": "1W",
    "stock.weekRangeNoData": "No 1-week trend data yet",
    "detail.invalidStockId": "Stock ID not found.",
    "detail.loading": "Loading stock details",
    "detail.selectHistoryRange": "Select history range",
    "detail.backToDashboard": "Back to dashboard",
    "detail.createAlert": "Create alert",
    "detail.twSignals.eyebrow": "Taiwan market signals",
    "detail.twSignals.title": "Institutional, ownership & margins",
    "detail.twSignals.description":
      "Taiwan institutional activity, margin / short changes, ownership concentration, and profitability margins. Institutional / margin / ownership prefer official sources; profitability margins are labeled Yahoo Finance.",
    "detail.twSignals.institutionalTitle": "Institutional activity",
    "detail.twSignals.marginShortTitle": "Margin / short changes",
    "detail.twSignals.ownershipTitle": "Ownership concentration",
    "detail.twSignals.profitMarginsTitle": "Profitability margins",
    "quote.updatedAt": "Updated {time}",
    "quote.open": "Open",
    "quote.high": "High",
    "quote.low": "Low",
    "quote.limitUp": "Limit up",
    "quote.limitDown": "Limit down",
    "chart.noData": "No chart data is available.",
    "chart.title": "Price history",
    "chart.rangeSummary": "{range} range {min} - {max}",
    "chart.description": "Chart shows candlesticks for the selected range.",
    "chart.axisYTitle": "Price ({currency})",
    "chart.axisDate": "Date",
    "chart.candleClose": "Close",
    "chart.interactHint": "Hover, click, or drag on the chart to inspect open, high, low, close, and volume for that time.",
    "chart.sma10": "SMA10",
    "chart.sma20": "SMA20",
    "chart.sma60": "SMA60",
    "chart.smaLegend": "Moving averages",
    "chart.smaDirectionUp": "Up",
    "chart.smaDirectionDown": "Down",
    "chart.smaDirectionFlat": "Flat",
    "rankings.title": "Market rankings",
    "rankings.description":
      "Free-only rankings. Both Taiwan and U.S. show quote-based rankings over a limited tracked universe rather than full-market data. Taiwan also includes daily institutional net buy/sell rankings.",
    "rankings.market": "Market",
    "rankings.market.us": "U.S.",
    "rankings.market.tw": "Taiwan",
    "rankings.loading": "Loading rankings",
    "rankings.empty": "No ranking rows are available for this selection right now.",
    "rankings.empty.limitUp": "No Taiwan stocks are at limit-up right now.",
    "rankings.empty.limitDown": "No Taiwan stocks are at limit-down right now.",
    "rankings.liveOnly": "This list is live-only and is not saved in the rankings archive.",
    "rankings.tab.volume": "Volume",
    "rankings.tab.volumeUp": "Volume up",
    "rankings.tab.volumeDown": "Volume down",
    "rankings.tab.changeUp": "Gainers",
    "rankings.tab.changeDown": "Losers",
    "rankings.tab.lowPe": "Low P/E",
    "rankings.tab.netBuy": "Net buy",
    "rankings.tab.netSell": "Net sell",
    "rankings.tab.limitUp": "Limit-up",
    "rankings.tab.limitDown": "Limit-down",
    "rankings.column.rank": "Rank",
    "rankings.column.symbol": "Symbol",
    "rankings.column.board": "Board",
    "rankings.column.name": "Name",
    "rankings.column.lastPrice": "Last price",
    "rankings.column.changePercent": "Change %",
    "rankings.column.volume": "Volume",
    "rankings.column.limitUpPrice": "Limit-up price",
    "rankings.column.limitDownPrice": "Limit-down price",
    "rankings.column.priceLimitStatus": "Limit",
    "rankings.column.netBuySell": "Net buy / sell",
    "rankings.column.peRatio": "P/E",
    "rankings.column.peRatioSource": "P/E source",
    "rankings.column.valuationAsOf": "Valuation as of",
    "rankings.column.dataTime": "Data time",
    "rankings.column.source": "Source",
    "rankings.meta.snapshotTimestamp": "Snapshot",
    "rankings.meta.source": "Source",
    "rankings.meta.sourceType": "Source type",
    "rankings.sourceType.freePublic": "Free public",
    "rankings.sourceType.freeUnofficial": "Free unofficial",
    "rankings.sourceType.cache": "Cache",
    "rankings.sourceType.unsupported": "Unsupported",
    "rankings.snapshotState.intraday": "30-minute intraday snapshot",
    "rankings.snapshotState.liveOnly": "Live only",
    "rankings.snapshotState.daily": "Daily TW institutional data",
    "rankings.snapshotState.eodArchive": "Archived end-of-day snapshot",
    "rankings.snapshotState.cache": "Latest cached snapshot",
    "rankings.snapshotState.unsupported": "Unsupported",
    "rankings.notice.usScopeYahoo":
      "U.S. quote rankings for this tab come pre-sorted from Yahoo Finance's predefined screeners and cover the broader U.S. market.",
    "rankings.notice.usScopeSp500":
      "U.S. gainers and losers are the largest regular-session % moves among S&P 500 constituents (committed index snapshot) using batched Yahoo Finance quote fields. U.S. volume still prefers Yahoo's broader most-actives screener when available.",
    "rankings.notice.usScopeQuote":
      "U.S. rankings for this tab use the app's configured free quote universe and live quotes; symbols without a usable quote are omitted.",
    "rankings.notice.usScopeDjia":
      "U.S. rankings for this tab sort the Dow Jones Industrial Average (DJIA) constituent universe using the app's free quote sources. The index has 30 names, so this table never pads extra symbols.",
    "rankings.notice.usScopeNasdaq100":
      "U.S. rankings for this tab sort the Nasdaq-100 constituent universe using the app's free quote sources.",
    "rankings.notice.usScopePending":
      "U.S. scope for this tab appears after its data finishes loading.",
    "rankings.notice.twInstScopeTwse":
      "Taiwan institutional rankings here are filtered to TWSE-listed (上市) names using TWSE daily T86 data.",
    "rankings.notice.twInstScopeTpex":
      "Taiwan institutional rankings here are filtered to TPEx-listed (上櫃) names using TPEx daily institutional detail data.",
    "rankings.notice.twInstScopeTpexUnavailable":
      "TPEx institutional rankings are temporarily unavailable from the free public source.",
    "rankings.notice.twQuoteScopeTwse":
      "Taiwan quote rankings for this tab are filtered to TWSE-listed (上市) names from the app's configured free quote universe.",
    "rankings.notice.twQuoteScopeTpex":
      "Taiwan quote rankings for this tab are filtered to TPEx-listed (上櫃) names from the app's configured free quote universe.",
    "rankings.twBoard.groupLabel": "Board",
    "rankings.twBoard.all": "All",
    "rankings.twBoard.twse": "Listed (TWSE)",
    "rankings.twBoard.tpex": "TPEx (OTC)",
    "rankings.usUniverse.groupLabel": "Index universe",
    "rankings.usUniverse.djia": "Dow Jones",
    "rankings.usUniverse.nasdaq100": "Nasdaq-100",
    "rankings.refresh.action": "Refresh from source",
    "rankings.refresh.loading": "Refreshing from source",
    "rankings.refresh.failed":
      "Could not refresh rankings from the upstream source. The current table was left unchanged.",
    "rankings.date.label": "Trading date",
    "rankings.date.current": "Today / Current",
    "rankings.date.historicalBanner":
      "Showing archived end-of-day rankings for {date}. This is historical data, not a live market view.",
    "rankings.date.loadHistory": "Load historical dates",
    "rankings.date.historyUnavailable":
      "Historical trading dates are temporarily unavailable. Current rankings still work.",
    "rankings.date.historyWaking":
      "Waking the historical archive… this can take a few seconds after idle.",
    "rankings.notice.quoteUnofficial": "Free unofficial quote source; data may be delayed or unavailable.",
    "rankings.notice.twDaily": "Taiwan institutional net buy/sell rankings use daily data, not intraday real-time data.",
    "rankings.notice.twDailyTpex":
      "TPEx institutional net buy/sell rankings use daily data, not intraday real-time data.",
    "rankings.notice.usInstitutionalUnsupported":
      "U.S. institutional net buy/sell is not available in this free-only app.",
    "rankings.notice.usPriceLimitUnsupported":
      "U.S. markets do not use Taiwan daily price-limit rankings in this app.",
    "rankings.notice.twPriceLimit":
      "Limit-up and limit-down lists use official TWSE MIS limit prices for TWSE and TPEx. A stock is included only when its traded price equals that limit, not because the change is about 10%.",
    "rankings.notice.twPriceLimitPartial":
      "Some Taiwan quotes could not be classified. Those symbols are omitted instead of guessing from the change percentage.",
    "rankings.notice.twPriceLimitUnavailable":
      "Taiwan limit-price quotes are temporarily unavailable. No symbols are listed, so a missing feed is not treated as a limit-up or limit-down ranking.",
    "rankings.freshness.twPriceLimitOpen":
      "Market open; Taiwan limit-up and limit-down rankings use official MIS limit prices and are live-only.",
    "rankings.freshness.twPriceLimitClosed":
      "Market closed; Taiwan limit-up and limit-down rankings use the latest MIS session limit prices and are live-only.",
    "rankings.freshness.unsupported": "Unsupported in this free-only app.",
    "rankings.source.twPriceLimit": "TWSE/TPEx MIS quotes with official daily limit prices.",
    "rankings.badge.limitUp": "Limit up",
    "rankings.badge.limitDown": "Limit down",
    "rankings.notice.twInstitutionalUnavailable":
      "Taiwan institutional daily data is temporarily unavailable from the free public source.",
    "rankings.notice.twInstitutionalTpexUnavailable":
      "TPEx institutional daily data is temporarily unavailable from the free public source.",
    "rankings.freshness.closedLatestSnapshot":
      "Market is closed. Showing latest available free snapshot.",
    "rankings.freshness.cachedCurrentBucket":
      "Showing the latest saved 30-minute free snapshot for this market window.",
    "rankings.freshness.cachedTwDaily":
      "Showing the latest saved TW institutional daily snapshot.",
    "rankings.freshness.freeSourceFallback":
      "Free data source temporarily unavailable. Showing latest available snapshot if available.",
    "rankings.freshness.noCachedSnapshot":
      "Free data source temporarily unavailable. No cached snapshot is available yet.",
    "rankings.freshness.quoteSnapshotOpen": "Market open; rankings use the latest quote snapshot.",
    "rankings.freshness.quoteSnapshotClosed": "Market closed; rankings use the latest available quote snapshot.",
    "rankings.freshness.changeSnapshotOpen":
      "Market open; price-move rankings use the latest quote snapshot.",
    "rankings.freshness.changeSnapshotClosed":
      "Market closed; price-move rankings use the latest available quote snapshot.",
    "rankings.freshness.yahooVolumeOpen":
      "Market open; volume rankings come from Yahoo Finance and refresh every few minutes.",
    "rankings.freshness.yahooVolumeClosed":
      "Market closed; volume rankings come from Yahoo Finance's latest snapshot.",
    "rankings.freshness.yahooChangeOpen":
      "Market open; price-move rankings come from Yahoo Finance and refresh every few minutes.",
    "rankings.freshness.yahooChangeClosed":
      "Market closed; price-move rankings come from Yahoo Finance's latest snapshot.",
    "rankings.freshness.sp500MoversOpen":
      "Market open; S&P 500 movers ranked by batched Yahoo quote change % (regular session fields).",
    "rankings.freshness.sp500MoversClosed":
      "Market closed; S&P 500 movers ranked by latest batched Yahoo quote change % (regular session fields).",
    "rankings.freshness.valuationLowPeOpen":
      "Market open; valuation rankings use the latest quote snapshot filtered to the highest-volume candidates, then sort by the latest available P/E.",
    "rankings.freshness.valuationLowPeClosed":
      "Market closed; valuation rankings use the latest available quote snapshot filtered to the highest-volume candidates, then sort by the latest available P/E.",
    "rankings.freshness.volumeBaselineMissing":
      "Baseline unavailable on the first snapshot; volume change percent stays empty until a later refresh.",
    "rankings.freshness.volumeBaselineOpen": "Volume change uses the previous quote snapshot baseline.",
    "rankings.freshness.volumeBaselineClosed":
      "Market closed; volume change uses the previous quote snapshot baseline.",
    "rankings.freshness.twInstitutionalUnavailable":
      "Taiwan daily institutional ranking is unavailable from the free public source right now.",
    "rankings.freshness.twInstitutionalTpexUnavailable":
      "TPEx daily institutional ranking is unavailable from the free public source right now.",
    "rankings.freshness.twNetBuy":
      "Taiwan daily institutional ranking sorted by highest institutional net buy/sell shares.",
    "rankings.freshness.twNetSell":
      "Taiwan daily institutional ranking sorted by most negative institutional net buy/sell shares.",
    "rankings.freshness.twNetBuyClosed":
      "Market closed; Taiwan daily institutional ranking sorted by highest institutional net buy/sell shares.",
    "rankings.freshness.twNetSellClosed":
      "Market closed; Taiwan daily institutional ranking sorted by most negative institutional net buy/sell shares.",
    "rankings.freshness.tpexNetBuy":
      "TPEx daily institutional ranking sorted by highest institutional net buy/sell shares.",
    "rankings.freshness.tpexNetSell":
      "TPEx daily institutional ranking sorted by most negative institutional net buy/sell shares.",
    "rankings.freshness.tpexNetBuyClosed":
      "Market closed; TPEx daily institutional ranking sorted by highest institutional net buy/sell shares.",
    "rankings.freshness.tpexNetSellClosed":
      "Market closed; TPEx daily institutional ranking sorted by most negative institutional net buy/sell shares.",
    "rankings.source.quoteProviders": "Quote rankings from the app's free best-effort quote providers.",
    "rankings.source.djiaQuote":
      "DJIA constituent quote rankings from the app's free best-effort quote providers.",
    "rankings.source.nasdaq100Quote":
      "Nasdaq-100 constituent quote rankings from the app's free best-effort quote providers.",
    "rankings.source.yahooPreRanked": "Yahoo Finance pre-ranked rankings.",
    "rankings.source.sp500Movers":
      "S&P 500 movers from a constituent snapshot plus batched Yahoo Finance quote fields (not Yahoo's predefined gainers/losers screeners).",
    "rankings.source.valuationUniverse":
      "Valuation rankings use the highest-volume current quote candidates, then sort by P/E from the app's available valuation sources.",
    "rankings.source.tpexInstitutional": "TPEx institutional daily data",
    "rankings.source.twse": "TWSE T86 daily institutional data",
    "rankings.source.unsupported": "Unsupported in this free-only app",
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
    ...portfolioMarketSectionMessages.en,
    "portfolio.search.eyebrow": "Holdings",
    "portfolio.search.title": "Add symbols to your portfolio",
    "portfolio.search.description": "Search U.S. and Taiwan stocks. Add only names you consider real holdings.",
    "portfolio.search.hint": "Results update as you type. Add or remove holdings without affecting your watchlist.",
    "portfolio.search.emptyPrompt": "Enter a symbol or company name to find stocks to add as holdings.",
    "portfolio.addHolding": "Add to portfolio",
    "portfolio.removeHolding": "Remove from portfolio",
    "portfolio.quantity.saving": "Saving...",
    "portfolio.quantity.saved": "Saved",
    "portfolio.ai.eyebrow": "AI analysis",
    "portfolio.ai.title": "AI portfolio analysis",
    "portfolio.ai.description":
      "The backend computes the portfolio summary first and sends only that summary to your saved AI provider.",
    "portfolio.ai.analyze": "Analyze my portfolio",
    "portfolio.ai.reanalyze": "Refresh analysis",
    "portfolio.ai.settings": "AI Settings",
    "portfolio.ai.loading": "Analyzing portfolio...",
    "portfolio.ai.notConfigured": "AI is not configured. Please set up your AI provider in AI Settings.",
    "portfolio.ai.emptyPortfolioError": "Your portfolio has no holdings to analyze.",
    "portfolio.ai.noAnalyzableHoldings":
      "No holdings could be analyzed right now. Review your positions (metadata, currency, and quotes) and try again.",
    "portfolio.ai.failed": "Could not load AI portfolio analysis. Try again.",
    "portfolio.ai.empty": "Add at least one holding before running AI portfolio analysis.",
    "portfolio.ai.totalMarketValueUsd": "Total market value (USD)",
    "portfolio.ai.totalMarketValueTwd": "Total market value (TWD)",
    "portfolio.ai.totalCostBasisUsd": "Total cost basis (USD)",
    "portfolio.ai.totalUnrealizedPlUsd": "Total unrealized P/L (USD)",
    "portfolio.ai.totalUnrealizedPlPercent": "Total unrealized P/L %",
    "portfolio.ai.concentration": "Concentration risk",
    "portfolio.ai.topHolding": "Top holding",
    "portfolio.ai.top3": "Top 3 holdings",
    "portfolio.ai.level": "Risk level",
    "portfolio.ai.level.low": "Low",
    "portfolio.ai.level.moderate": "Moderate",
    "portfolio.ai.level.high": "High",
    "portfolio.ai.exposure": "Currency exposure",
    "portfolio.ai.largestHoldings": "Largest holdings",
    "portfolio.ai.report": "AI report",
    "portfolio.ai.noReport":
      "The provider summary is unavailable right now. The computed portfolio metrics below are still current.",
    "portfolio.ai.noReportProviderError":
      "The AI provider did not return a narrative (see Important notices above). Check AI Settings, quotas, and server logs, then use Refresh analysis to retry. Computed metrics below are still current.",
    "portfolio.ai.noReportSeeWarnings":
      "No AI narrative was returned. Review Important notices above, then try Refresh analysis. Computed metrics below are still current.",
    "portfolio.ai.warningsHeading": "Important notices",
    "portfolio.ai.skipped": "Skipped holdings",
    "portfolio.ai.symbol": "Symbol",
    "portfolio.ai.weight": "Weight %",
    "portfolio.ai.marketValue": "Market value",
    "portfolio.ai.unrealizedPl": "Unrealized P/L",
    "stock.ai.eyebrow": "AI",
    "stock.ai.title": "AI stock analysis",
    "stock.ai.description":
      "Computed metrics refresh when you open this page. The AI narrative is loaded from cache until you choose Refresh analysis.",
    "stock.ai.refresh": "Refresh analysis",
    "stock.ai.loading": "Loading stock analysis...",
    "stock.ai.refreshing": "Refreshing AI analysis...",
    "stock.ai.refreshFailed": "Could not refresh AI analysis. Try again.",
    "stock.ai.loadFailed":
      "Failed to load stock analysis. Check the backend analysis configuration and try again.",
    "stock.ai.explanation": "AI explanation",
    "stock.ai.noCache":
      "No cached AI analysis is available yet. Click Refresh analysis to generate one with your configured AI provider. Computed metrics above are based on the latest market data loaded for this page.",
    "stock.ai.sources": "Sources",
    "stock.ai.analyzedAt": "AI narrative as of {datetime}",
    "stock.ai.disclaimer":
      "This analysis is for informational purposes only and is not financial advice.",
    "stock.ai.notice.historyFetchFailed":
      "Could not load one year of daily price history for this stock.",
    "stock.ai.notice.noHistory": "No historical price data was returned for this stock.",
    "stock.ai.notice.unusableHistory":
      "The historical price data did not include usable closing prices.",
    "stock.ai.notice.metadataUnavailable":
      "Some company details could not be loaded, so parts of the analysis may be incomplete.",
    "stock.ai.notice.invalidTicker":
      "This ticker appears to be invalid or is currently unavailable.",
    "stock.ai.notice.aiSkippedNoHistory":
      "AI analysis was skipped because the required price history is unavailable.",
    "stock.ai.notice.newsUnavailable":
      "Recent news context could not be loaded, so this analysis uses market data only.",
    "stock.ai.metric.ticker": "Ticker",
    "stock.ai.metric.company": "Company",
    "stock.ai.metric.currency": "Currency",
    "stock.ai.metric.sector": "Sector",
    "stock.ai.metric.industry": "Industry",
    "stock.ai.metric.marketCap": "Market cap",
    "stock.ai.metric.trailingPe": "Trailing P/E",
    "stock.ai.metric.forwardPe": "Forward P/E",
    "stock.ai.metric.currentPrice": "Current price",
    "stock.ai.metric.return1m": "1M return",
    "stock.ai.metric.return3m": "3M return",
    "stock.ai.metric.return6m": "6M return",
    "stock.ai.metric.return1y": "1Y return",
    "stock.ai.metric.sma50": "50D SMA",
    "stock.ai.metric.sma200": "200D SMA",
    "stock.ai.metric.priceVsSma50": "Price vs 50D SMA",
    "stock.ai.metric.priceVsSma200": "Price vs 200D SMA",
    "stock.ai.metric.rsi14": "14D RSI",
    "stock.ai.metric.volatility30d": "30D annualized volatility",
    "stock.ai.metric.maxDrawdown1y": "1Y max drawdown",
    "stock.ai.metric.averageVolume30d": "30D average volume",
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
    "portfolio.aggregate.totalCostBasis": "Total cost basis",
    "portfolio.aggregate.totalGain": "Total gain / loss",
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
    "aiSettings.description":
      "Choose which provider runs portfolio and stock AI analysis, then save API keys for each provider.",
    "aiSettings.providersEyebrow": "AI providers",
    "aiSettings.providersIntro":
      "The provider dropdown for keys only picks which key you edit. Use Active provider below to switch OpenAI vs Perplexity for analysis.",
    "aiSettings.activeProviderTitle": "Active provider for analysis",
    "aiSettings.activeProviderHelp":
      "Portfolio and stock AI use this provider when its API key is saved. Click Save after changing.",
    "aiSettings.activeProviderLabel": "Active provider",
    "aiSettings.activeProviderCurrent": "Currently active: {provider}",
    "aiSettings.activeProviderAuto": "OpenAI first, then Perplexity (default)",
    "aiSettings.saveActiveProvider": "Save active provider",
    "aiSettings.apiKeysTitle": "API keys",
    "aiSettings.keyProviderLabel": "Provider for key entry",
    "aiSettings.keyConfigured": "{provider} key saved (****{last4})",
    "aiSettings.keyNotConfigured": "{provider} key not saved yet",
    "aiSettings.openAiStatus": "OpenAI",
    "aiSettings.perplexityStatus": "Perplexity",
    "aiSettings.notConfiguredShort": "not set",
    "aiSettings.apiKeyLabel": "{provider} API key",
    "aiSettings.apiKeyPlaceholder": "Paste API key",
    "aiSettings.saveApiKey": "Save API key",
    "aiSettings.checking": "Checking...",
    "aiSettings.saving": "Saving...",
    "twInsights.title": "Taiwan fundamentals snapshot",
    "twInsights.description":
      "Additional Taiwan-only context similar to local investor portals: revenue trend, peer momentum, and dividends.",
    "twInsights.loading": "Loading Taiwan insights",
    "twInsights.unavailable": "Taiwan insights are not available for this symbol right now.",
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
    "portfolio.column.averageCost": "Avg cost (stock currency)",
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
    ...twQuoteFallbackMessages("en"),
  },
  "zh-TW": {
    "nav.dashboard": "儀表板",
    "nav.alerts": "警示",
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
    "marketSnapshot.title": "市場快覽",
    "marketSnapshot.eyebrow": "重要指標",
    "marketSnapshot.fxLabel": "美金／台幣匯率",
    "marketSnapshot.oilLabel": "WTI 原油",
    "marketSnapshot.perBarrel": "/ 桶",
    "marketSnapshot.updated": "更新",
    "marketSnapshot.loading": "載入市場快覽中",
    "marketSnapshot.source": "來源",
    "marketSnapshot.twdUnit": "新台幣",
    "marketSnapshot.stale": "過舊",
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
    "watchlist.manageAlerts": "管理警示",
    "watchlist.market.tw": "台灣股票",
    "watchlist.market.us": "美股",
    "watchlist.market.unknown": "其他代碼",
    "watchlist.sectionCount": "{count} 檔",
    "watchlist.sectionCount.one": "{count} 檔",
    "alerts.title": "門檻警示",
    "alerts.description": "建立價格高於或低於門檻的規則，並集中查看最近觸發紀錄。",
    "alerts.form.eyebrow": "規則編輯器",
    "alerts.form.createTitle": "新增警示規則",
    "alerts.form.editTitle": "編輯警示規則",
    "alerts.form.stockId": "股票代號",
    "alerts.form.stockIdPlaceholder": "例如 AAPL、2330，或 US:AAPL",
    "alerts.form.stockIdHelper": "可輸入一般代號（如 AAPL / 2330），或標準 ID（如 US:AAPL）。",
    "alerts.form.ruleType": "規則類型",
    "alerts.form.threshold": "門檻價格",
    "alerts.form.enabled": "規則狀態",
    "alerts.form.submitCreate": "建立警示",
    "alerts.form.submitUpdate": "儲存變更",
    "alerts.form.cancelEdit": "取消",
    "alerts.error.unknownStock": "找不到股票。請輸入代號，或使用標準 ID（例如 US:AAPL）。",
    "alerts.error.unknownStockId":
      "找不到股票 {stockId}。請輸入代號，或使用標準 ID（例如 US:AAPL）。",
    "alerts.error.ambiguousStock":
      "股票代號「{query}」有多筆相符，請選擇：{choices}",
    "alerts.rules.title": "目前規則",
    "alerts.rules.empty": "目前沒有警示規則。請先在上方建立一筆。",
    "alerts.events.title": "最近事件",
    "alerts.events.empty": "目前沒有警示事件。當規則跨越門檻時，紀錄會出現在這裡。",
    "alerts.events.clearHistory": "清除紀錄",
    "alerts.events.clearConfirmTitle": "清除警示事件紀錄",
    "alerts.events.clearConfirmMessage": "確定要清除所有警示事件紀錄嗎？這不會刪除警示規則。",
    "alerts.events.clearConfirmCancel": "取消",
    "alerts.events.clearConfirmAction": "清除紀錄",
    "alerts.ruleType.priceAbove": "價格高於",
    "alerts.ruleType.priceBelow": "價格低於",
    "alerts.status.enabled": "啟用中",
    "alerts.status.disabled": "已停用",
    "alerts.status.conditionMet": "條件成立",
    "alerts.status.monitoring": "監控中",
    "alerts.actions.edit": "編輯",
    "alerts.actions.pause": "暫停",
    "alerts.actions.resume": "恢復",
    "alerts.actions.delete": "刪除",
    "alerts.column.stock": "股票",
    "alerts.column.rule": "規則",
    "alerts.column.threshold": "門檻",
    "alerts.column.status": "狀態",
    "alerts.column.lastObserved": "最近觀測",
    "alerts.column.lastTriggered": "最近觸發",
    "alerts.column.actions": "操作",
    "alerts.column.observedPrice": "觀測價格",
    "alerts.column.triggeredAt": "觸發時間",
    "alerts.column.message": "訊息",
    ...realizedGainsMessages("zh-TW"),
    "stock.market": "市場",
    "stock.updatedAt": "更新時間",
    "stock.prevClose": "前收",
    "stock.volume": "成交量",
    "stock.viewDetails": "檢視明細",
    "stock.remove": "移除",
    "stock.add": "加入",
    "stock.quoteLoadError": "目前無法載入報價。",
    "stock.quoteLoading": "載入報價中",
    "stock.quoteRefreshing": "更新報價中",
    "stock.quoteUnavailableTitle": "目前無法載入報價",
    "stock.quoteUnavailableBody": "外部資料來源暫時無法使用，請稍後再試。",
    "stock.quoteRetry": "重新整理",
    "stock.weekRange": "近一週",
    "stock.weekRangeNoData": "近一週走勢暫時無資料",
    "detail.invalidStockId": "找不到股票代碼。",
    "detail.loading": "載入股票明細中",
    "detail.selectHistoryRange": "選擇歷史區間",
    "detail.backToDashboard": "返回儀表板",
    "detail.createAlert": "建立警示",
    "detail.twSignals.eyebrow": "台股市場訊號",
    "detail.twSignals.title": "法人、籌碼與財報三率",
    "detail.twSignals.description":
      "此區塊顯示台股三大法人日買賣超、資券變化、籌碼集中度與財報三率；法人／資券／籌碼以官方來源為主，財報三率標示 Yahoo Finance。",
    "detail.twSignals.institutionalTitle": "法人動態",
    "detail.twSignals.marginShortTitle": "資券變化",
    "detail.twSignals.ownershipTitle": "籌碼集中度",
    "detail.twSignals.profitMarginsTitle": "財報三率",
    "quote.updatedAt": "更新時間 {time}",
    "quote.open": "開盤",
    "quote.high": "最高",
    "quote.low": "最低",
    "quote.limitUp": "漲停",
    "quote.limitDown": "跌停",
    "chart.noData": "沒有可顯示的圖表資料。",
    "chart.title": "價格走勢",
    "chart.rangeSummary": "{range} 區間高低 {min} - {max}",
    "chart.description": "圖表以 K 線顯示所選區間的價格走勢。",
    "chart.axisYTitle": "價格（{currency}）",
    "chart.axisDate": "日期",
    "chart.candleClose": "收盤",
    "chart.interactHint": "將游標移到圖表上、點選或拖曳，可檢視該時間點的開高低收與成交量。",
    "chart.sma10": "SMA10",
    "chart.sma20": "SMA20",
    "chart.sma60": "SMA60",
    "chart.smaLegend": "移動平均線",
    "chart.smaDirectionUp": "上漲",
    "chart.smaDirectionDown": "下跌",
    "chart.smaDirectionFlat": "持平",
    "rankings.title": "市場排行",
    "rankings.description":
      "免費排行頁。台股與美股皆提供以報價資料為主的排行（目前僅涵蓋有限追蹤清單），並非全市場排行；台股另保留三大法人日買超／賣超。",
    "rankings.market": "市場",
    "rankings.market.us": "美股",
    "rankings.market.tw": "台股",
    "rankings.loading": "載入排行中",
    "rankings.empty": "目前沒有可顯示的排行資料。",
    "rankings.empty.limitUp": "目前沒有漲停股票",
    "rankings.empty.limitDown": "目前沒有跌停股票",
    "rankings.liveOnly": "此清單僅提供即時查詢，不會寫入排行封存。",
    "rankings.tab.volume": "成交量",
    "rankings.tab.volumeUp": "量增",
    "rankings.tab.volumeDown": "量減",
    "rankings.tab.changeUp": "漲幅",
    "rankings.tab.changeDown": "跌幅",
    "rankings.tab.lowPe": "低本益比",
    "rankings.tab.netBuy": "買超",
    "rankings.tab.netSell": "賣超",
    "rankings.tab.limitUp": "漲停板",
    "rankings.tab.limitDown": "跌停板",
    "rankings.column.rank": "排名",
    "rankings.column.symbol": "代碼",
    "rankings.column.board": "板別",
    "rankings.column.name": "名稱",
    "rankings.column.lastPrice": "最新價",
    "rankings.column.changePercent": "漲跌幅",
    "rankings.column.volume": "成交量",
    "rankings.column.limitUpPrice": "漲停價",
    "rankings.column.limitDownPrice": "跌停價",
    "rankings.column.priceLimitStatus": "漲跌停",
    "rankings.column.netBuySell": "買賣超",
    "rankings.column.peRatio": "本益比",
    "rankings.column.peRatioSource": "本益比來源",
    "rankings.column.valuationAsOf": "估值日期",
    "rankings.column.dataTime": "資料時間",
    "rankings.column.source": "來源",
    "rankings.meta.snapshotTimestamp": "快照時間",
    "rankings.meta.source": "資料來源",
    "rankings.meta.sourceType": "來源類型",
    "rankings.sourceType.freePublic": "免費公開",
    "rankings.sourceType.freeUnofficial": "免費非官方",
    "rankings.sourceType.cache": "快取",
    "rankings.sourceType.unsupported": "不支援",
    "rankings.snapshotState.intraday": "30 分鐘盤中快照",
    "rankings.snapshotState.liveOnly": "即時查詢",
    "rankings.snapshotState.daily": "台股法人日資料",
    "rankings.snapshotState.eodArchive": "已封存收盤快照",
    "rankings.snapshotState.cache": "最新快取快照",
    "rankings.snapshotState.unsupported": "不支援",
    "rankings.notice.usScopeYahoo": "美股此分頁採用 Yahoo Finance 預先排序的選股結果，涵蓋更廣的美股市場。",
    "rankings.notice.usScopeSp500":
      "美股漲跌幅排行以標普五百成分股（內建名單快照）為範圍，使用 Yahoo Finance 批次報價欄位排序；美股成交量仍優先採用 Yahoo 全市場熱門成交選股（若可用）。",
    "rankings.notice.usScopeQuote":
      "美股此分頁使用本應用程式設定的免費報價清單與即時報價排序；無法取得有效報價的代碼會略過。",
    "rankings.notice.usScopeDjia":
      "美股此分頁以道瓊工業平均指數（DJIA）成分股為範圍，使用本應用程式的免費報價來源排序。指數僅有 30 檔，不會為湊滿列數而補上其他代碼。",
    "rankings.notice.usScopeNasdaq100":
      "美股此分頁以 Nasdaq-100 成分股為範圍，使用本應用程式的免費報價來源排序。",
    "rankings.notice.usScopePending": "美股此分頁的資料範圍說明會在資料載入完成後顯示。",
    "rankings.notice.twInstScopeTwse": "此處的台股法人排行僅含上市（TWSE）股票，資料來自 TWSE 法人日報表。",
    "rankings.notice.twInstScopeTpex":
      "此處的台股法人排行僅含上櫃（TPEx）股票，資料來自 TPEx 法人日明細資料。",
    "rankings.notice.twInstScopeTpexUnavailable":
      "目前無法從免費公開來源取得上櫃（TPEx）法人排行。",
    "rankings.notice.twQuoteScopeTwse":
      "此分頁的台股報價排行僅含上市（TWSE）且來自本應用程式設定的免費報價清單。",
    "rankings.notice.twQuoteScopeTpex":
      "此分頁的台股報價排行僅含上櫃（TPEx）且來自本應用程式設定的免費報價清單。",
    "rankings.twBoard.groupLabel": "交易別",
    "rankings.twBoard.all": "全部",
    "rankings.twBoard.twse": "上市",
    "rankings.twBoard.tpex": "上櫃",
    "rankings.usUniverse.groupLabel": "指數範圍",
    "rankings.usUniverse.djia": "道瓊",
    "rankings.usUniverse.nasdaq100": "Nasdaq-100",
    "rankings.refresh.action": "重新抓取最新資料",
    "rankings.refresh.loading": "正在重新抓取最新資料",
    "rankings.refresh.failed": "無法從上游來源重新抓取排行；目前表格資料維持不變。",
    "rankings.date.label": "交易日",
    "rankings.date.current": "今日／即時",
    "rankings.date.historicalBanner":
      "目前顯示 {date} 的收盤歷史排行（非即時盤中資料）。",
    "rankings.date.loadHistory": "載入歷史交易日",
    "rankings.date.historyUnavailable":
      "暫時無法載入歷史交易日；今日／即時排行仍可正常使用。",
    "rankings.date.historyWaking":
      "正在喚醒歷史封存資料庫，閒置後可能需要數秒…",
    "rankings.notice.quoteUnofficial": "免費非官方報價來源可能延遲，或暫時無法提供資料。",
    "rankings.notice.twDaily": "台股三大法人買賣超為日資料，非盤中即時資料。",
    "rankings.notice.twDailyTpex": "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。",
    "rankings.notice.usInstitutionalUnsupported": "此免費版本不提供美股法人買超／賣超。",
    "rankings.notice.usPriceLimitUnsupported": "此應用程式的漲停／跌停排行只適用台股。",
    "rankings.notice.twPriceLimit":
      "漲停板與跌停板使用證交所 MIS 公布的漲跌停價。只有成交價等於該價格才會列入，不會因為漲跌幅約為 10% 就判定。",
    "rankings.notice.twPriceLimitPartial":
      "部分台股報價無法完成漲跌停判定，這些股票已略過，不會用漲跌幅百分比來猜測。",
    "rankings.notice.twPriceLimitUnavailable":
      "暫時無法取得台股漲跌停報價。為避免把普通漲跌誤判成漲停或跌停，目前不列出任何股票。",
    "rankings.freshness.twPriceLimitOpen":
      "市場開盤中；漲停與跌停排行使用 MIS 官方漲跌停價，且僅提供即時查詢。",
    "rankings.freshness.twPriceLimitClosed":
      "市場休市；漲停與跌停排行使用 MIS 最近一個交易時段的漲跌停價，且僅提供即時查詢。",
    "rankings.freshness.unsupported": "此免費版本不支援此排行。",
    "rankings.source.twPriceLimit": "上市／上櫃 MIS 報價與官方漲跌停價。",
    "rankings.badge.limitUp": "漲停",
    "rankings.badge.limitDown": "跌停",
    "rankings.notice.twInstitutionalUnavailable": "目前無法從免費公開來源取得台股法人日資料。",
    "rankings.notice.twInstitutionalTpexUnavailable":
      "目前無法從免費公開來源取得上櫃（TPEx）法人日資料。",
    "rankings.freshness.closedLatestSnapshot": "市場休市；顯示最新可得的免費快照。",
    "rankings.freshness.cachedCurrentBucket": "顯示此市場區間內最近儲存的 30 分鐘免費快照。",
    "rankings.freshness.cachedTwDaily": "顯示最近儲存的台股法人日快照。",
    "rankings.freshness.freeSourceFallback": "免費資料來源暫時無法使用；若有快照則顯示最新可得快照。",
    "rankings.freshness.noCachedSnapshot": "免費資料來源暫時無法使用，且目前沒有可用的快照。",
    "rankings.freshness.quoteSnapshotOpen": "市場開盤中；排行使用最新報價快照。",
    "rankings.freshness.quoteSnapshotClosed": "市場休市；排行使用最新可得的報價快照。",
    "rankings.freshness.changeSnapshotOpen": "市場開盤中；漲跌幅排行使用最新報價快照。",
    "rankings.freshness.changeSnapshotClosed": "市場休市；漲跌幅排行使用最新可得的報價快照。",
    "rankings.freshness.yahooVolumeOpen":
      "市場開盤中；成交量排行來自 Yahoo Finance，約每幾分鐘自動更新。",
    "rankings.freshness.yahooVolumeClosed": "市場休市；成交量排行來自 Yahoo Finance 的最新快照。",
    "rankings.freshness.yahooChangeOpen":
      "市場開盤中；漲跌幅排行來自 Yahoo Finance，約每幾分鐘自動更新。",
    "rankings.freshness.yahooChangeClosed": "市場休市；漲跌幅排行來自 Yahoo Finance 的最新快照。",
    "rankings.freshness.sp500MoversOpen":
      "市場開盤中；標普五百漲跌幅依 Yahoo 批次報價的盤中漲跌幅欄位排序。",
    "rankings.freshness.sp500MoversClosed":
      "市場休市；標普五百漲跌幅依 Yahoo 批次報價的最新盤中漲跌幅欄位排序。",
    "rankings.freshness.valuationLowPeOpen":
      "市場開盤中；低本益比排行先取最新報價快照中的高成交量候選，再依目前可取得的本益比排序。",
    "rankings.freshness.valuationLowPeClosed":
      "市場休市；低本益比排行先取最新可得的報價快照中的高成交量候選，再依目前可取得的本益比排序。",
    "rankings.freshness.volumeBaselineMissing": "首次快照尚無基準值；量變化百分比會在後續刷新後顯示。",
    "rankings.freshness.volumeBaselineOpen": "量變化使用前一次報價快照作為基準。",
    "rankings.freshness.volumeBaselineClosed": "市場休市；量變化使用前一次報價快照作為基準。",
    "rankings.freshness.twInstitutionalUnavailable": "目前無法從免費公開來源取得台股法人日排行。",
    "rankings.freshness.twInstitutionalTpexUnavailable":
      "目前無法從免費公開來源取得上櫃（TPEx）法人日排行。",
    "rankings.freshness.twNetBuy": "台股法人日排行依三大法人買賣超股數由高到低排序。",
    "rankings.freshness.twNetSell": "台股法人日排行依三大法人買賣超股數由低到高排序。",
    "rankings.freshness.twNetBuyClosed": "市場休市；台股法人日排行依三大法人買賣超股數由高到低排序。",
    "rankings.freshness.twNetSellClosed": "市場休市；台股法人日排行依三大法人買賣超股數由低到高排序。",
    "rankings.freshness.tpexNetBuy": "上櫃（TPEx）法人日排行依三大法人買賣超股數由高到低排序。",
    "rankings.freshness.tpexNetSell": "上櫃（TPEx）法人日排行依三大法人買賣超股數由低到高排序。",
    "rankings.freshness.tpexNetBuyClosed":
      "市場休市；上櫃（TPEx）法人日排行依三大法人買賣超股數由高到低排序。",
    "rankings.freshness.tpexNetSellClosed":
      "市場休市；上櫃（TPEx）法人日排行依三大法人買賣超股數由低到高排序。",
    "rankings.source.quoteProviders": "排行使用本應用程式的免費最佳努力報價來源。",
    "rankings.source.djiaQuote": "道瓊工業平均指數成分股排行，使用本應用程式的免費最佳努力報價來源。",
    "rankings.source.nasdaq100Quote": "Nasdaq-100 成分股排行，使用本應用程式的免費最佳努力報價來源。",
    "rankings.source.yahooPreRanked": "Yahoo Finance 預先排序的排行資料。",
    "rankings.source.sp500Movers":
      "標普五百漲跌幅：成分股快照＋Yahoo Finance 批次報價欄位（非 Yahoo 預設漲跌榜選股）。",
    "rankings.source.valuationUniverse":
      "估值排行先取目前高成交量的報價候選，再依本應用程式可取得的本益比資料排序。",
    "rankings.source.tpexInstitutional": "TPEx 法人日資料",
    "rankings.source.twse": "TWSE T86 法人日資料",
    "rankings.source.unsupported": "此免費版本不支援",
    "portfolio.title": "投資組合",
    "portfolio.description":
      "此處為您實際持有的標的，與儀表板的「自選清單」分開；自選清單適合研究與追蹤。",
    "portfolio.equity.title": "持股市值（美金與台幣）",
    "portfolio.count": "{count} 檔持倉",
    "portfolio.loading": "載入投資組合報價中",
    "portfolio.loadError": "無法載入持倉。請確認後端已啟動後再試。",
    "portfolio.empty": "尚無持倉。請在下方搜尋並加入您持有的標的。",
    "portfolio.table.currencyHint": "最新價與持倉市值皆依各檔股票原幣顯示，例如 USD 或 TWD。",
    ...portfolioMarketSectionMessages["zh-TW"],
    "portfolio.search.eyebrow": "持倉",
    "portfolio.search.title": "將標的加入投資組合",
    "portfolio.search.description": "搜尋美股與台股，僅加入您視為實際持倉的標的。",
    "portfolio.search.hint": "輸入後即時更新。加入或移除持倉不會影響自選清單。",
    "portfolio.search.emptyPrompt": "輸入代碼或公司名稱，找出要加入為持倉的標的。",
    "portfolio.addHolding": "加入投資組合",
    "portfolio.removeHolding": "從投資組合移除",
    "portfolio.quantity.saving": "儲存中...",
    "portfolio.quantity.saved": "已儲存",
    "portfolio.ai.eyebrow": "AI 分析",
    "portfolio.ai.title": "AI 投資組合分析",
    "portfolio.ai.description": "系統會先由後端計算投資組合摘要，再只把摘要送到您已儲存的 AI 提供方。",
    "portfolio.ai.analyze": "分析我的投資組合",
    "portfolio.ai.reanalyze": "重新分析",
    "portfolio.ai.settings": "AI 設定",
    "portfolio.ai.loading": "分析投資組合中...",
    "portfolio.ai.notConfigured":
      "尚未設定 AI。請至「AI 設定」頁面設定您的 AI 提供方。",
    "portfolio.ai.emptyPortfolioError": "投資組合目前沒有持倉可分析。",
    "portfolio.ai.noAnalyzableHoldings":
      "目前沒有任何持倉可供分析。請檢查持倉資料、幣別與報價是否完整後再試。",
    "portfolio.ai.failed": "目前無法載入 AI 投資組合分析，請稍後再試。",
    "portfolio.ai.empty": "請至少先新增一筆持倉，再執行 AI 投資組合分析。",
    "portfolio.ai.totalMarketValueUsd": "總市值（USD）",
    "portfolio.ai.totalMarketValueTwd": "總市值（TWD）",
    "portfolio.ai.totalCostBasisUsd": "總成本（USD）",
    "portfolio.ai.totalUnrealizedPlUsd": "未實現損益（USD）",
    "portfolio.ai.totalUnrealizedPlPercent": "未實現損益 %",
    "portfolio.ai.concentration": "集中度風險",
    "portfolio.ai.topHolding": "最大持股",
    "portfolio.ai.top3": "前三大持股",
    "portfolio.ai.level": "風險等級",
    "portfolio.ai.level.low": "低",
    "portfolio.ai.level.moderate": "中",
    "portfolio.ai.level.high": "高",
    "portfolio.ai.exposure": "幣別曝險",
    "portfolio.ai.largestHoldings": "最大持股",
    "portfolio.ai.report": "AI 報告",
    "portfolio.ai.noReport": "目前無法取得 AI 文字報告，但下方後端計算的投資組合指標仍為最新結果。",
    "portfolio.ai.noReportProviderError":
      "AI 提供方這次沒有回傳文字報告（請見上方「重要提示」）。請檢查 AI 設定、用量上限與伺服器紀錄，並可點「重新分析」再試；下方後端計算的投資組合指標仍為最新結果。",
    "portfolio.ai.noReportSeeWarnings":
      "未取得 AI 文字報告。請先查看上方「重要提示」，再按「重新分析」；下方後端計算的投資組合指標仍為最新結果。",
    "portfolio.ai.warningsHeading": "重要提示",
    "portfolio.ai.skipped": "已略過持股",
    "portfolio.ai.symbol": "代碼",
    "portfolio.ai.weight": "權重 %",
    "portfolio.ai.marketValue": "市值",
    "portfolio.ai.unrealizedPl": "未實現損益",
    "stock.ai.eyebrow": "AI",
    "stock.ai.title": "AI 個股分析",
    "stock.ai.description":
      "開啟此頁時會更新計算指標。AI 文字分析會從快取載入，直到您選擇「重新分析」。",
    "stock.ai.refresh": "重新分析",
    "stock.ai.loading": "載入個股分析中...",
    "stock.ai.refreshing": "正在重新產生 AI 分析...",
    "stock.ai.refreshFailed": "無法重新產生 AI 分析，請再試一次。",
    "stock.ai.loadFailed": "無法載入個股分析。請檢查後端分析設定後再試。",
    "stock.ai.explanation": "AI 說明",
    "stock.ai.noCache":
      "目前尚無快取的 AI 分析。請點「重新分析」，以您設定的 AI 提供方產生說明。上方計算指標仍依本頁載入的最新市場資料。",
    "stock.ai.sources": "來源",
    "stock.ai.analyzedAt": "AI 文字分析時間：{datetime}",
    "stock.ai.disclaimer": "本分析僅供參考，不構成投資建議。",
    "stock.ai.notice.historyFetchFailed": "無法載入這檔股票近一年的日線價格資料。",
    "stock.ai.notice.noHistory": "查無這檔股票的歷史價格資料。",
    "stock.ai.notice.unusableHistory": "歷史價格資料中沒有可用的收盤價。",
    "stock.ai.notice.metadataUnavailable": "部分公司資料無法載入，分析內容可能不完整。",
    "stock.ai.notice.invalidTicker": "這個代碼可能無效，或目前無法取得資料。",
    "stock.ai.notice.aiSkippedNoHistory": "因為缺少必要的價格歷史資料，已略過 AI 分析。",
    "stock.ai.notice.newsUnavailable": "無法載入近期新聞背景，本次分析僅依據市場資料。",
    "stock.ai.metric.ticker": "代碼",
    "stock.ai.metric.company": "公司",
    "stock.ai.metric.currency": "幣別",
    "stock.ai.metric.sector": "產業",
    "stock.ai.metric.industry": "細產業",
    "stock.ai.metric.marketCap": "市值",
    "stock.ai.metric.trailingPe": "本益比（過去）",
    "stock.ai.metric.forwardPe": "本益比（預期）",
    "stock.ai.metric.currentPrice": "目前價格",
    "stock.ai.metric.return1m": "近 1 月報酬",
    "stock.ai.metric.return3m": "近 3 月報酬",
    "stock.ai.metric.return6m": "近 6 月報酬",
    "stock.ai.metric.return1y": "近 1 年報酬",
    "stock.ai.metric.sma50": "50 日均線",
    "stock.ai.metric.sma200": "200 日均線",
    "stock.ai.metric.priceVsSma50": "相對 50 日均線",
    "stock.ai.metric.priceVsSma200": "相對 200 日均線",
    "stock.ai.metric.rsi14": "14 日 RSI",
    "stock.ai.metric.volatility30d": "30 日年化波動",
    "stock.ai.metric.maxDrawdown1y": "近 1 年最大回撤",
    "stock.ai.metric.averageVolume30d": "30 日平均成交量",
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
    "portfolio.aggregate.totalCostBasis": "總成本",
    "portfolio.aggregate.totalGain": "總損益",
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
    "aiSettings.description": "選擇投資組合與個股 AI 分析使用的提供方，並儲存各提供方的 API 金鑰。",
    "aiSettings.providersEyebrow": "AI 提供方",
    "aiSettings.providersIntro":
      "下方「金鑰輸入」的下拉選單只決定要編輯哪一家的金鑰。請在「使用中提供方」選擇實際用於分析的 OpenAI 或 Perplexity。",
    "aiSettings.activeProviderTitle": "使用中提供方（分析用）",
    "aiSettings.activeProviderHelp": "投資組合與個股 AI 會使用此提供方（需已儲存對應 API 金鑰）。變更後請按儲存。",
    "aiSettings.activeProviderLabel": "使用中提供方",
    "aiSettings.activeProviderCurrent": "目前使用中：{provider}",
    "aiSettings.activeProviderAuto": "預設：先 OpenAI，再 Perplexity",
    "aiSettings.saveActiveProvider": "儲存使用中提供方",
    "aiSettings.apiKeysTitle": "API 金鑰",
    "aiSettings.keyProviderLabel": "要編輯金鑰的提供方",
    "aiSettings.keyConfigured": "已儲存 {provider} 金鑰（****{last4}）",
    "aiSettings.keyNotConfigured": "尚未儲存 {provider} 金鑰",
    "aiSettings.openAiStatus": "OpenAI",
    "aiSettings.perplexityStatus": "Perplexity",
    "aiSettings.notConfiguredShort": "未設定",
    "aiSettings.apiKeyLabel": "{provider} API 金鑰",
    "aiSettings.apiKeyPlaceholder": "貼上 API 金鑰",
    "aiSettings.saveApiKey": "儲存 API 金鑰",
    "aiSettings.checking": "檢查中...",
    "aiSettings.saving": "儲存中...",
    "twInsights.title": "台股基本面快照",
    "twInsights.description": "僅台股顯示：月營收趨勢、同業表現與股利資訊。",
    "twInsights.loading": "載入台股補充資訊中",
    "twInsights.unavailable": "目前無法顯示此股票的台股補充資訊。",
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
    "portfolio.column.averageCost": "平均成本（原幣）",
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
    ...twQuoteFallbackMessages("zh-TW"),
  },
};

type LanguageContextValue = {
  language: AppLanguage;
  languageReady: boolean;
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
  const [languageReady, setLanguageReady] = useState(false);

  // Keep first client render aligned with SSR output, then apply saved preference.
  // This avoids hydration mismatch when localStorage language differs.
  useEffect(() => {
    setLanguageState(readStoredLanguage());
    setLanguageReady(true);
  }, []);

  const value = useMemo<LanguageContextValue>(
    () => ({
      language,
      languageReady,
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
    [language, languageReady],
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
