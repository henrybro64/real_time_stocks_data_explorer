import type { RankingResponse } from "@shared/api";

type RankingsCopyKey =
  | "rankings.notice.twDaily"
  | "rankings.notice.twInstitutionalUnavailable"
  | "rankings.freshness.twInstitutionalUnavailable"
  | "rankings.freshness.twNetBuy"
  | "rankings.freshness.twNetSell"
  | "rankings.freshness.twNetBuyClosed"
  | "rankings.freshness.twNetSellClosed"
  | "rankings.source.twse"
  | "rankings.source.unsupported";

const FRESHNESS_LABEL_KEYS: Record<string, RankingsCopyKey> = {
  "TW daily institutional ranking is unavailable from the free public source right now.":
    "rankings.freshness.twInstitutionalUnavailable",
  "TW daily institutional ranking sorted by highest 三大法人買賣超股數.": "rankings.freshness.twNetBuy",
  "TW daily institutional ranking sorted by most negative 三大法人買賣超股數.": "rankings.freshness.twNetSell",
  "Market closed; TW daily institutional ranking sorted by highest 三大法人買賣超股數.":
    "rankings.freshness.twNetBuyClosed",
  "Market closed; TW daily institutional ranking sorted by most negative 三大法人買賣超股數.":
    "rankings.freshness.twNetSellClosed",
};

const WARNING_MESSAGE_KEYS: Record<string, RankingsCopyKey> = {
  "台股三大法人買賣超為日資料，非盤中即時資料。": "rankings.notice.twDaily",
};

const UNSUPPORTED_REASON_KEYS: Record<string, RankingsCopyKey> = {
  "TW institutional daily data is temporarily unavailable from the free public source.":
    "rankings.notice.twInstitutionalUnavailable",
};

const SOURCE_KEYS: Record<string, RankingsCopyKey> = {
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
