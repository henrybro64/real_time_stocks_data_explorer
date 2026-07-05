import type { RankingResponse } from "@shared/api";

type RankingsCopyKey =
  | "rankings.notice.quoteUnofficial"
  | "rankings.notice.twDaily"
  | "rankings.notice.twDailyTpex"
  | "rankings.notice.twInstitutionalUnavailable"
  | "rankings.notice.twInstitutionalTpexUnavailable"
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
  | "rankings.source.quoteProviders"
  | "rankings.source.yahooPreRanked"
  | "rankings.source.sp500Movers"
  | "rankings.source.tpexInstitutional"
  | "rankings.source.twse"
  | "rankings.source.unsupported";

const FRESHNESS_LABEL_KEYS: Record<string, RankingsCopyKey> = {
  "Market open; rankings use the latest quote snapshot.": "rankings.freshness.quoteSnapshotOpen",
  "Market closed; rankings use the latest available quote snapshot.": "rankings.freshness.quoteSnapshotClosed",
  "Market open; price-move rankings use the latest quote snapshot.":
    "rankings.freshness.changeSnapshotOpen",
  "Market closed; price-move rankings use the latest available quote snapshot.":
    "rankings.freshness.changeSnapshotClosed",
  "Market open; volume rankings come from Yahoo Finance and refresh every few minutes.":
    "rankings.freshness.yahooVolumeOpen",
  "Market closed; volume rankings come from Yahoo Finance's latest snapshot.":
    "rankings.freshness.yahooVolumeClosed",
  "Market open; price-move rankings come from Yahoo Finance and refresh every few minutes.":
    "rankings.freshness.yahooChangeOpen",
  "Market closed; price-move rankings come from Yahoo Finance's latest snapshot.":
    "rankings.freshness.yahooChangeClosed",
  "Market open; S&P 500 movers ranked by batched Yahoo quote change % (regular session fields).":
    "rankings.freshness.sp500MoversOpen",
  "Market closed; S&P 500 movers ranked by latest batched Yahoo quote change % (regular session fields).":
    "rankings.freshness.sp500MoversClosed",
  "Baseline unavailable on the first snapshot; volume change percent stays empty until a later refresh.":
    "rankings.freshness.volumeBaselineMissing",
  "Volume change uses the previous quote snapshot baseline.": "rankings.freshness.volumeBaselineOpen",
  "Market closed; volume change uses the previous quote snapshot baseline.":
    "rankings.freshness.volumeBaselineClosed",
  "TW daily institutional ranking is unavailable from the free public source right now.":
    "rankings.freshness.twInstitutionalUnavailable",
  "TPEx institutional daily ranking is unavailable from the free public source right now.":
    "rankings.freshness.twInstitutionalTpexUnavailable",
  "TW daily institutional ranking sorted by highest 三大法人買賣超股數.": "rankings.freshness.twNetBuy",
  "TW daily institutional ranking sorted by most negative 三大法人買賣超股數.": "rankings.freshness.twNetSell",
  "Market closed; TW daily institutional ranking sorted by highest 三大法人買賣超股數.":
    "rankings.freshness.twNetBuyClosed",
  "Market closed; TW daily institutional ranking sorted by most negative 三大法人買賣超股數.":
    "rankings.freshness.twNetSellClosed",
  "TPEx daily institutional ranking sorted by highest 三大法人買賣超股數.":
    "rankings.freshness.tpexNetBuy",
  "TPEx daily institutional ranking sorted by most negative 三大法人買賣超股數.":
    "rankings.freshness.tpexNetSell",
  "Market closed; TPEx daily institutional ranking sorted by highest 三大法人買賣超股數.":
    "rankings.freshness.tpexNetBuyClosed",
  "Market closed; TPEx daily institutional ranking sorted by most negative 三大法人買賣超股數.":
    "rankings.freshness.tpexNetSellClosed",
};

const WARNING_MESSAGE_KEYS: Record<string, RankingsCopyKey> = {
  "Free unofficial quote source; data may be delayed or unavailable.": "rankings.notice.quoteUnofficial",
  "台股三大法人買賣超為日資料，非盤中即時資料。": "rankings.notice.twDaily",
  "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。": "rankings.notice.twDailyTpex",
};

const UNSUPPORTED_REASON_KEYS: Record<string, RankingsCopyKey> = {
  "TW institutional daily data is temporarily unavailable from the free public source.":
    "rankings.notice.twInstitutionalUnavailable",
  "TPEx institutional daily data is temporarily unavailable from the free public source.":
    "rankings.notice.twInstitutionalTpexUnavailable",
};

const SOURCE_KEYS: Record<string, RankingsCopyKey> = {
  "Quote rankings from the app's free best-effort quote providers.": "rankings.source.quoteProviders",
  "Yahoo Finance pre-ranked quote rankings.": "rankings.source.yahooPreRanked",
  "S&P 500 movers: constituent snapshot plus batched Yahoo Finance v7 quote fields (free best-effort; not the same universe as Yahoo's predefined gainers/losers screeners).":
    "rankings.source.sp500Movers",
  "TPEx institutional daily data": "rankings.source.tpexInstitutional",
  "TWSE T86 daily institutional data": "rankings.source.twse",
  "Unsupported in Phase 1 free-only rankings": "rankings.source.unsupported",
};

type TranslateRankingsCopy = (key: RankingsCopyKey) => string;

function localizeKnownString(
  value: string | null | undefined,
  mapping: Record<string, RankingsCopyKey>,
  translate: TranslateRankingsCopy,
) {
  if (!value) {
    return value ?? null;
  }
  const key = mapping[value];
  return key ? translate(key) : value;
}

export function getLocalizedRankingsFreshnessLabel(
  response: RankingResponse,
  translate: TranslateRankingsCopy,
) {
  return localizeKnownString(response.dataFreshnessLabel, FRESHNESS_LABEL_KEYS, translate);
}

export function getLocalizedRankingsWarningMessage(
  response: RankingResponse,
  translate: TranslateRankingsCopy,
) {
  return localizeKnownString(response.warningMessage, WARNING_MESSAGE_KEYS, translate);
}

export function getLocalizedRankingsUnsupportedReason(
  response: RankingResponse,
  translate: TranslateRankingsCopy,
) {
  return localizeKnownString(response.unsupportedReason, UNSUPPORTED_REASON_KEYS, translate);
}

export function getLocalizedRankingsSource(
  response: RankingResponse,
  translate: TranslateRankingsCopy,
) {
  return localizeKnownString(response.source, SOURCE_KEYS, translate) ?? response.source;
}

export function isRankingsCopyKey(key: string): key is RankingsCopyKey {
  return key.startsWith("rankings.notice.")
    || key.startsWith("rankings.freshness.")
    || key.startsWith("rankings.source.");
}

export type { RankingsCopyKey };
