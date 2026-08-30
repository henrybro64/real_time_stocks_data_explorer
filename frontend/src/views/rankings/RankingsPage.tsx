"use client";

import {
  Button,
  Column,
  Grid,
  InlineLoading,
  Select,
  SelectItem,
  Tag,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tile,
} from "@carbon/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MarketStatus, RankingResponse, RankingType, RankingUniverse } from "@shared/api";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { getDisplayStockName, useLanguage, type TranslationKey } from "../../app/i18n";
import { QuoteFreshnessTag } from "../../components/state/QuoteFreshnessTag";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import {
  fetchMarkets,
  fetchRankings,
  fetchRankingsHistory,
  fetchRankingsHistoryDates,
  historyDatesRetryDelayMs,
  refreshRankings,
  shouldRetryHistoryDatesQuery,
  shouldRetryRankingsQuery,
  toApiError,
} from "../../services/api/client";
import { formatCurrency, formatLastUpdated, formatPercent } from "../../services/format/quote";
import { priceToneClassForMarket } from "../../services/market-data/priceTone";
import {
  getLocalizedRankingsFreshnessLabel,
  getLocalizedRankingsSource,
  getLocalizedRankingsUnsupportedReason,
  getLocalizedRankingsWarningMessage,
} from "./rankingsCopy";
import { defaultRankingsMarketFromStatuses } from "./defaultRankingsMarket";

const DEFAULT_LIMIT = 50;
const DEFAULT_MARKET = "TW" as const;
const HISTORY_DATES_LIMIT = 5;
/** Sentinel value for the date selector: live/current rankings. */
export const RANKINGS_DATE_CURRENT = "current";
export const RANKINGS_REFRESH_COOLDOWN_MS = 15_000;
type RankingMarket = RankingResponse["market"];
type TwBoardSegment = "TWSE" | "TPEx";
type UsUniverseSegment = "DJIA" | "NASDAQ100";

type RankingsRefreshVars = {
  market: RankingMarket;
  rankingType: RankingType;
  universe: RankingUniverse;
};

function rankingsQueryKey(
  market: RankingMarket,
  universe: RankingUniverse,
  rankingType: RankingType,
  tradingDate: string | null = null,
) {
  return ["rankings", market, universe, rankingType, DEFAULT_LIMIT, tradingDate ?? RANKINGS_DATE_CURRENT] as const;
}

function rankingsHistoryDatesQueryKey(
  market: RankingMarket,
  universe: RankingUniverse,
  rankingType: RankingType,
) {
  return ["rankings-history-dates", market, universe, rankingType, HISTORY_DATES_LIMIT] as const;
}

type RankingsScopeToggleProps<T extends string> = Readonly<{
  groupLabel?: string;
  value: T;
  options: Array<{ value: T; testId: string; label: string }>;
  onChange: (next: T) => void;
  className?: string;
  style?: CSSProperties;
}>;

function RankingsScopeToggle<T extends string>({
  groupLabel,
  value,
  options,
  onChange,
  className = "app-shell-brand",
  style = { marginTop: "0.75rem" },
}: RankingsScopeToggleProps<T>) {
  return (
    <div className={className} style={style}>
      {groupLabel ? (
        <p className="app-eyebrow" style={{ marginBottom: "0.35rem" }}>
          {groupLabel}
        </p>
      ) : null}
      <div className="app-auth-actions" style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
        {options.map((option) => (
          <Button
            key={option.value}
            data-testid={option.testId}
            kind={value === option.value ? "primary" : "ghost"}
            size="sm"
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>
    </div>
  );
}

type UsScopeNoticeKey =
  | "rankings.notice.usScopeYahoo"
  | "rankings.notice.usScopeSp500"
  | "rankings.notice.usScopeQuote"
  | "rankings.notice.usScopeDjia"
  | "rankings.notice.usScopeNasdaq100"
  | "rankings.notice.usInstitutionalUnsupported"
  | "rankings.notice.usScopePending";

type TwQuoteScopeNoticeKey =
  | "rankings.notice.twQuoteScopeYahooTwse"
  | "rankings.notice.twQuoteScopeYahooTpex"
  | "rankings.notice.twQuoteScopeTwse"
  | "rankings.notice.twQuoteScopeTpex"
  | "rankings.notice.twQuoteScopeFallbackTwse"
  | "rankings.notice.twQuoteScopeFallbackTpex";

const TW_QUOTE_SCOPE_NOTICE: Record<
  "yahoo" | "fallback" | "quote",
  { TWSE: TwQuoteScopeNoticeKey; TPEx: TwQuoteScopeNoticeKey }
> = {
  yahoo: {
    TWSE: "rankings.notice.twQuoteScopeYahooTwse",
    TPEx: "rankings.notice.twQuoteScopeYahooTpex",
  },
  fallback: {
    TWSE: "rankings.notice.twQuoteScopeFallbackTwse",
    TPEx: "rankings.notice.twQuoteScopeFallbackTpex",
  },
  quote: {
    TWSE: "rankings.notice.twQuoteScopeTwse",
    TPEx: "rankings.notice.twQuoteScopeTpex",
  },
};

type RankingsTabLabelKey =
  | "rankings.tab.volume"
  | "rankings.tab.volumeUp"
  | "rankings.tab.volumeDown"
  | "rankings.tab.changeUp"
  | "rankings.tab.changeDown"
  | "rankings.tab.lowPe"
  | "rankings.tab.netBuy"
  | "rankings.tab.netSell";

type RankingsSourceTypeLabelKey =
  | "rankings.sourceType.freePublic"
  | "rankings.sourceType.freeUnofficial"
  | "rankings.sourceType.cache"
  | "rankings.sourceType.unsupported";

type RankingsSnapshotStateLabelKey =
  | "rankings.snapshotState.intraday"
  | "rankings.snapshotState.daily"
  | "rankings.snapshotState.eodArchive"
  | "rankings.snapshotState.cache"
  | "rankings.snapshotState.unsupported";

const TW_RANKING_TABS: Array<{ key: RankingType; labelKey: RankingsTabLabelKey }> = [
  { key: "volume", labelKey: "rankings.tab.volume" },
  { key: "valuation_low_pe", labelKey: "rankings.tab.lowPe" },
  { key: "change_up", labelKey: "rankings.tab.changeUp" },
  { key: "change_down", labelKey: "rankings.tab.changeDown" },
  { key: "net_buy", labelKey: "rankings.tab.netBuy" },
  { key: "net_sell", labelKey: "rankings.tab.netSell" },
];

const US_RANKING_TABS: Array<{ key: RankingType; labelKey: RankingsTabLabelKey }> = [
  { key: "volume", labelKey: "rankings.tab.volume" },
  { key: "valuation_low_pe", labelKey: "rankings.tab.lowPe" },
  { key: "change_up", labelKey: "rankings.tab.changeUp" },
  { key: "change_down", labelKey: "rankings.tab.changeDown" },
  { key: "net_buy", labelKey: "rankings.tab.netBuy" },
  { key: "net_sell", labelKey: "rankings.tab.netSell" },
];

function tabsForMarket(market: RankingMarket) {
  return market === "US" ? US_RANKING_TABS : TW_RANKING_TABS;
}

function defaultRankingTypeForMarket(market: RankingMarket): RankingType {
  return market === "US" ? "volume" : "net_buy";
}

function isInstitutionalRanking(rankingType: RankingType) {
  return rankingType === "net_buy" || rankingType === "net_sell";
}

function isValuationRanking(rankingType: RankingType) {
  return rankingType === "valuation_low_pe";
}

function usScopeNoticeKeyFromState(
  rankingType: RankingType,
  data: RankingResponse | undefined,
): UsScopeNoticeKey {
  const aligned =
    data != null && data.market === "US" && data.rankingType === rankingType;
  if (!aligned) {
    return "rankings.notice.usScopePending";
  }
  if (isInstitutionalRanking(rankingType) && data.unsupportedReason) {
    return "rankings.notice.usInstitutionalUnsupported";
  }
  switch (data.usRankingsScope) {
    case "djia":
      return "rankings.notice.usScopeDjia";
    case "nasdaq100":
      return "rankings.notice.usScopeNasdaq100";
    case "sp500":
      return "rankings.notice.usScopeSp500";
    case "yahoo":
      return "rankings.notice.usScopeYahoo";
    case "quote":
      return "rankings.notice.usScopeQuote";
    default:
      return "rankings.notice.usScopePending";
  }
}

function twQuoteScopeNoticeKeyFromState(
  rankingType: RankingType,
  twBoard: TwBoardSegment,
  data: RankingResponse | undefined,
): TwQuoteScopeNoticeKey {
  const aligned = data?.market === "TW" && data.rankingType === rankingType;
  const scope = aligned && data?.twRankingsScope ? data.twRankingsScope : "quote";
  const keys = TW_QUOTE_SCOPE_NOTICE[scope] ?? TW_QUOTE_SCOPE_NOTICE.quote;
  return keys[twBoard];
}

export function getRankingsRefetchInterval(isMarketOpen: boolean, rankingType: RankingType) {
  if (isInstitutionalRanking(rankingType)) {
    return 12 * 60 * 60 * 1000;
  }
  return isMarketOpen ? 30 * 60 * 1000 : 2 * 60 * 60 * 1000;
}

function formatMaybePercent(value: number | null) {
  if (value == null) {
    return "—";
  }
  return formatPercent(value);
}

function formatMaybeCurrency(value: number | null, currency: "USD" | "TWD") {
  if (value == null) {
    return "—";
  }
  return `${currency} ${formatCurrency(value, currency)}`;
}

function formatMaybeNumber(value: number | null, formatNumber: (value: number) => string) {
  if (value == null) {
    return "—";
  }
  return formatNumber(value);
}

function formatMaybePeRatio(value: number | null | undefined, language: "en" | "zh-TW") {
  if (value == null) {
    return "—";
  }
  return new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatMaybeText(value: string | null | undefined) {
  return value && value.trim() ? value : "—";
}

function tableTitle(
  response: RankingResponse | undefined,
  fallback: string,
  translate: (key: RankingsTabLabelKey | "rankings.market.tw" | "rankings.market.us") => string,
) {
  if (!response) {
    return fallback;
  }
  const tab = tabsForMarket(response.market).find((candidate) => candidate.key === response.rankingType);
  const rankingLabel = tab ? translate(tab.labelKey) : response.rankingType;
  const marketLabel = response.market === "US" ? translate("rankings.market.us") : translate("rankings.market.tw");
  return `${marketLabel} ${rankingLabel}`;
}

function rankingsSourceTypeLabelKey(
  sourceType: RankingResponse["sourceType"],
): RankingsSourceTypeLabelKey {
  switch (sourceType) {
    case "free-public":
      return "rankings.sourceType.freePublic";
    case "free-unofficial":
      return "rankings.sourceType.freeUnofficial";
    case "cache":
      return "rankings.sourceType.cache";
    default:
      return "rankings.sourceType.unsupported";
  }
}

function rankingsSnapshotStateLabelKey(
  response: RankingResponse,
): RankingsSnapshotStateLabelKey {
  if (response.unsupportedReason) {
    return "rankings.snapshotState.unsupported";
  }
  if (response.tradingDate != null || response.captureScope === "end_of_day") {
    return "rankings.snapshotState.eodArchive";
  }
  if (response.sourceType === "cache") {
    return "rankings.snapshotState.cache";
  }
  if (response.market === "TW" && isInstitutionalRanking(response.rankingType)) {
    return "rankings.snapshotState.daily";
  }
  return "rankings.snapshotState.intraday";
}

function rankingsSourceTypeTagType(
  sourceType: RankingResponse["sourceType"],
): "green" | "purple" | "blue" | "gray" {
  switch (sourceType) {
    case "free-public":
      return "green";
    case "free-unofficial":
      return "purple";
    case "cache":
      return "blue";
    default:
      return "gray";
  }
}

function getLatestRankingTimestamp(response: RankingResponse) {
  if (response.items.length === 0) {
    return null;
  }
  return response.items.reduce((latestTimestamp, item) => {
    const latestTime = Date.parse(latestTimestamp);
    const itemTime = Date.parse(item.timestamp);
    if (Number.isNaN(itemTime)) {
      return latestTimestamp;
    }
    if (Number.isNaN(latestTime) || itemTime > latestTime) {
      return item.timestamp;
    }
    return latestTimestamp;
  }, response.items[0].timestamp);
}

function RankingRealtimeTag({ response }: { response: RankingResponse }) {
  const { language } = useLanguage();
  if (isInstitutionalRanking(response.rankingType)) {
    const label = language === "zh-TW" ? "非即時" : "Not real-time";

    return (
      <Tag data-testid="rankings-realtime-tag" size="sm" type="cool-gray">
        {label}
      </Tag>
    );
  }

  const latestTimestamp = getLatestRankingTimestamp(response);
  if (latestTimestamp == null) {
    return null;
  }

  return (
    <div data-testid="rankings-realtime-tag">
      <QuoteFreshnessTag isMarketOpen={response.isMarketOpen} timestamp={latestTimestamp} />
    </div>
  );
}

export function RankingsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { language, t, formatNumber } = useLanguage();
  const [marketOverride, setMarketOverride] = useState<RankingMarket | null>(null);
  const marketsQuery = useQuery<MarketStatus[]>({
    queryKey: ["markets"],
    queryFn: fetchMarkets,
    staleTime: 60_000,
  });
  const implicitMarket = useMemo((): RankingMarket => {
    const list = marketsQuery.data;
    if (!list?.length) return DEFAULT_MARKET;
    return defaultRankingsMarketFromStatuses(list);
  }, [marketsQuery.data]);
  const market = marketOverride ?? implicitMarket;
  const [twBoard, setTwBoard] = useState<TwBoardSegment>("TWSE");
  const [usUniverse, setUsUniverse] = useState<UsUniverseSegment>("DJIA");
  const [rankingType, setRankingType] = useState<RankingType>(defaultRankingTypeForMarket(DEFAULT_MARKET));
  const [selectedTradingDate, setSelectedTradingDate] = useState<string | null>(null);
  /** Opt-in key `market:universe:type` — Current view must not wake Azure (#187). */
  const [historyDatesOptInKey, setHistoryDatesOptInKey] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshCooldownUntil, setRefreshCooldownUntil] = useState(0);
  const rankingTabs = tabsForMarket(market);
  const selectedScope: RankingUniverse = market === "TW" ? twBoard : usUniverse;
  const historyContextKey = `${market}:${selectedScope}:${rankingType}`;
  const historyDatesRequested = historyDatesOptInKey === historyContextKey;
  const isHistorical = selectedTradingDate != null;
  const rankingsQueryKeyValue = rankingsQueryKey(
    market,
    selectedScope,
    rankingType,
    selectedTradingDate,
  );

  useEffect(() => {
    setRankingType((prev) => {
      if (market === "US" && isInstitutionalRanking(prev)) {
        return defaultRankingTypeForMarket(market);
      }
      const allowed = new Set(tabsForMarket(market).map((tab) => tab.key));
      if (allowed.has(prev)) return prev;
      return defaultRankingTypeForMarket(market);
    });
  }, [market]);

  // Clear opt-in whenever ranking context changes so returning to a prior
  // market/universe/type does not auto-wake Azure from a stale key (#187).
  useEffect(() => {
    setHistoryDatesOptInKey(null);
  }, [historyContextKey]);

  const historyDatesQuery = useQuery({
    queryKey: rankingsHistoryDatesQueryKey(market, selectedScope, rankingType),
    queryFn: ({ signal }) =>
      fetchRankingsHistoryDates(
        market,
        selectedScope,
        rankingType,
        HISTORY_DATES_LIMIT,
        signal,
      ),
    // Lazy: only after explicit opt-in (or while viewing a historical date).
    enabled: historyDatesRequested || selectedTradingDate != null,
    staleTime: 5 * 60_000,
    retry: shouldRetryHistoryDatesQuery,
    retryDelay: (failureCount, error) => historyDatesRetryDelayMs(failureCount, error),
  });
  const availableHistoryDates = historyDatesQuery.data?.dates ?? [];
  const historyDatesRetrying =
    historyDatesQuery.isFetching
    && historyDatesQuery.failureCount > 0
    && historyDatesQuery.data === undefined;
  const historyDatesUnavailable =
    historyDatesQuery.isError
    && !historyDatesQuery.isFetching
    && historyDatesQuery.data === undefined;
  const dateOptions = useMemo(() => {
    if (
      selectedTradingDate != null
      && !availableHistoryDates.includes(selectedTradingDate)
    ) {
      return [selectedTradingDate, ...availableHistoryDates];
    }
    return availableHistoryDates;
  }, [availableHistoryDates, selectedTradingDate]);

  useEffect(() => {
    if (selectedTradingDate == null) {
      return;
    }
    // Query key changes with ranking type; wait for the new dates list before
    // deciding the selection is invalid (avoid flashing back to Current).
    if (!historyDatesQuery.isSuccess) {
      return;
    }
    if (!availableHistoryDates.includes(selectedTradingDate)) {
      setSelectedTradingDate(null);
    }
  }, [availableHistoryDates, historyDatesQuery.isSuccess, selectedTradingDate]);

  const rankingsQuery = useQuery<RankingResponse>({
    queryKey: rankingsQueryKeyValue,
    queryFn: ({ signal }) => {
      if (selectedTradingDate != null) {
        return fetchRankingsHistory(
          market,
          rankingType,
          selectedTradingDate,
          DEFAULT_LIMIT,
          selectedScope,
          signal,
        );
      }
      return fetchRankings(
        market,
        rankingType,
        DEFAULT_LIMIT,
        selectedScope,
        signal,
      );
    },
    // Historical reads hit Azure SQL; reuse the AutoPause retry budget (#177).
    // Live rankings keep QueryClient default retryDelay (0 in tests).
    retry: (failureCount, error) =>
      selectedTradingDate != null
        ? shouldRetryHistoryDatesQuery(failureCount, error)
        : shouldRetryRankingsQuery(failureCount, error),
    ...(selectedTradingDate != null
      ? {
          retryDelay: (failureCount: number, error: unknown) =>
            historyDatesRetryDelayMs(failureCount, error),
        }
      : {}),
    refetchInterval: (query) => {
      if (selectedTradingDate != null) {
        return false;
      }
      return getRankingsRefetchInterval(
        (query.state.data as RankingResponse | undefined)?.isMarketOpen ?? false,
        rankingType,
      );
    },
  });

  const refreshMutation = useMutation({
    mutationFn: (vars: RankingsRefreshVars) =>
      refreshRankings(vars.market, vars.rankingType, vars.universe, DEFAULT_LIMIT),
    onSuccess: (next, vars) => {
      setRefreshError(null);
      queryClient.setQueryData(
        rankingsQueryKey(vars.market, vars.universe, vars.rankingType, null),
        next,
      );
    },
    onError: (error) => {
      const apiError = toApiError(error);
      setRefreshError(apiError.detail ?? t("rankings.refresh.failed"));
    },
    onSettled: () => {
      const until = Date.now() + RANKINGS_REFRESH_COOLDOWN_MS;
      setRefreshCooldownUntil(until);
      window.setTimeout(() => {
        setRefreshCooldownUntil((current) => (current === until ? 0 : current));
      }, RANKINGS_REFRESH_COOLDOWN_MS);
    },
  });

  const data = rankingsQuery.data;
  const rankingsError = rankingsQuery.error != null ? toApiError(rankingsQuery.error) : null;
  const showError = rankingsError != null && data == null;
  const showUnsupported = !!data?.unsupportedReason;
  const localizedSource = data ? getLocalizedRankingsSource(data, t) : "";
  const localizedSourceType = data ? t(rankingsSourceTypeLabelKey(data.sourceType)) : "";
  const localizedSnapshotState = data ? t(rankingsSnapshotStateLabelKey(data)) : "";
  const showNetBuySellColumn = isInstitutionalRanking(rankingType);
  const showValuationColumns = isValuationRanking(rankingType);
  const refreshDisabled =
    isHistorical || refreshMutation.isPending || Date.now() < refreshCooldownUntil;
  // Historical views use the EOD banner; live scope notices describe current providers only.
  let scopeNoteKey: TranslationKey | null = null;
  if (!isHistorical) {
    if (market === "US") {
      scopeNoteKey = usScopeNoticeKeyFromState(rankingType, data);
    } else if (isInstitutionalRanking(rankingType)) {
      if (twBoard === "TPEx") {
        scopeNoteKey = showUnsupported
          ? "rankings.notice.twInstScopeTpexUnavailable"
          : "rankings.notice.twInstScopeTpex";
      } else {
        scopeNoteKey = "rankings.notice.twInstScopeTwse";
      }
    } else {
      scopeNoteKey = twQuoteScopeNoticeKeyFromState(rankingType, twBoard, data);
    }
  }

  function handleMarketChange(nextMarket: RankingMarket) {
    setMarketOverride(nextMarket);
    setRankingType(defaultRankingTypeForMarket(nextMarket));
    setSelectedTradingDate(null);
    setRefreshError(null);
    if (nextMarket === "TW") {
      setTwBoard("TWSE");
    } else {
      setUsUniverse("DJIA");
    }
  }

  function handleDateChange(next: string) {
    setRefreshError(null);
    setSelectedTradingDate(next === RANKINGS_DATE_CURRENT ? null : next);
  }

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-hero-tile app-portfolio-hero">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("rankings.title")}</h1>
            <p className="app-body-copy app-hero-description">{t("rankings.description")}</p>
          </div>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile app-rankings-controls">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("rankings.market")}</p>
              <h2>{t("rankings.title")}</h2>
            </div>
            {data && !isHistorical ? <RankingRealtimeTag response={data} /> : null}
          </div>
          <div className="app-portfolio-asset-form">
            <Select
              id="rankings-market-select"
              data-testid="rankings-market-select"
              labelText={t("rankings.market")}
              value={market}
              onChange={(event) => handleMarketChange(event.currentTarget.value as RankingMarket)}
            >
              <SelectItem value="TW" text={t("rankings.market.tw")} />
              <SelectItem value="US" text={t("rankings.market.us")} />
            </Select>
            {scopeNoteKey ? (
              <p className="app-supporting-copy" data-testid="rankings-scope-note">
                {t(scopeNoteKey)}
              </p>
            ) : null}
          </div>
          {market === "TW" ? (
            <RankingsScopeToggle
              groupLabel={t("rankings.twBoard.groupLabel")}
              value={twBoard}
              options={[
                {
                  value: "TWSE",
                  testId: "rankings-tw-board-twse",
                  label: t("rankings.twBoard.twse"),
                },
                {
                  value: "TPEx",
                  testId: "rankings-tw-board-tpex",
                  label: t("rankings.twBoard.tpex"),
                },
              ]}
              onChange={(next) => {
                setRefreshError(null);
                setSelectedTradingDate(null);
                setTwBoard(next);
              }}
            />
          ) : (
            <RankingsScopeToggle
              groupLabel={t("rankings.usUniverse.groupLabel")}
              value={usUniverse}
              options={[
                {
                  value: "DJIA",
                  testId: "rankings-us-universe-djia",
                  label: t("rankings.usUniverse.djia"),
                },
                {
                  value: "NASDAQ100",
                  testId: "rankings-us-universe-nasdaq100",
                  label: t("rankings.usUniverse.nasdaq100"),
                },
              ]}
              onChange={(next) => {
                setRefreshError(null);
                setSelectedTradingDate(null);
                setUsUniverse(next);
              }}
            />
          )}
          <Select
            id="rankings-date-select"
            data-testid="rankings-date-select"
            labelText={t("rankings.date.label")}
            value={selectedTradingDate ?? RANKINGS_DATE_CURRENT}
            onChange={(event) => handleDateChange(event.currentTarget.value)}
            style={{ marginTop: "1rem", maxWidth: "22rem" }}
          >
            <SelectItem value={RANKINGS_DATE_CURRENT} text={t("rankings.date.current")} />
            {dateOptions.map((tradingDate) => (
              <SelectItem key={tradingDate} value={tradingDate} text={tradingDate} />
            ))}
          </Select>
          {!historyDatesRequested && selectedTradingDate == null ? (
            <Button
              kind="ghost"
              size="sm"
              data-testid="rankings-history-dates-load"
              style={{ marginTop: "0.5rem" }}
              onClick={() => setHistoryDatesOptInKey(historyContextKey)}
            >
              {t("rankings.date.loadHistory")}
            </Button>
          ) : null}
          {historyDatesRetrying ? (
            <p className="app-supporting-copy" data-testid="rankings-history-dates-waking">
              {t("rankings.date.historyWaking")}
            </p>
          ) : null}
          {historyDatesUnavailable ? (
            <p className="app-supporting-copy" data-testid="rankings-history-dates-unavailable">
              {t("rankings.date.historyUnavailable")}
            </p>
          ) : null}
          <RankingsScopeToggle
            className="app-shell-brand app-rankings-tabs"
            style={{ marginTop: "1rem" }}
            value={rankingType}
            options={rankingTabs.map((tab) => ({
              value: tab.key,
              testId: `rankings-tab-${tab.key}`,
              label: t(tab.labelKey),
            }))}
            onChange={setRankingType}
          />
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          {showError ? (
            <RequestStateNotice
              detail={rankingsError.detail}
              kind={errorToNoticeKind(rankingsError)}
              scopeLabel={t("rankings.title")}
              status={rankingsError.status}
            />
          ) : rankingsQuery.isLoading && data == null ? (
            <InlineLoading description={t("rankings.loading")} status="active" />
          ) : (
            <>
              {data ? (
                <>
                  {isHistorical ? (
                    <p className="app-section-meta" data-testid="rankings-historical-banner">
                      {t("rankings.date.historicalBanner").replace(
                        "{date}",
                        data.tradingDate ?? selectedTradingDate ?? "",
                      )}
                    </p>
                  ) : null}
                  <p className="app-section-meta" data-testid="rankings-freshness-label">
                    {getLocalizedRankingsFreshnessLabel(data, t)}
                  </p>
                  <div
                    className="app-auth-actions"
                    style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", alignItems: "center", marginTop: "0.75rem" }}
                  >
                    <p className="app-section-meta" data-testid="rankings-snapshot-timestamp">
                      {t("rankings.meta.snapshotTimestamp")}: {formatLastUpdated(data.timestamp)}
                    </p>
                    <p className="app-section-meta" data-testid="rankings-source-label">
                      {t("rankings.meta.source")}: {localizedSource}
                    </p>
                    {!isHistorical ? (
                      <Button
                        data-testid="rankings-refresh-source"
                        kind="tertiary"
                        size="sm"
                        disabled={refreshDisabled}
                        onClick={() =>
                          refreshMutation.mutate({
                            market,
                            rankingType,
                            universe: selectedScope,
                          })
                        }
                      >
                        {refreshMutation.isPending ? t("rankings.refresh.loading") : t("rankings.refresh.action")}
                      </Button>
                    ) : null}
                    <Tag
                      data-testid="rankings-source-type-tag"
                      size="sm"
                      type={rankingsSourceTypeTagType(data.sourceType)}
                    >
                      {t("rankings.meta.sourceType")}: {localizedSourceType}
                    </Tag>
                    <Tag data-testid="rankings-snapshot-state-tag" size="sm" type="cool-gray">
                      {localizedSnapshotState}
                    </Tag>
                  </div>
                  {data.warningMessage ? (
                    <p className="app-section-meta" data-testid="rankings-warning-message">
                      {getLocalizedRankingsWarningMessage(data, t)}
                    </p>
                  ) : null}
                  {showUnsupported ? (
                    <p className="app-supporting-copy" data-testid="rankings-unsupported-message">
                      {getLocalizedRankingsUnsupportedReason(data, t)}
                    </p>
                  ) : null}
                  {refreshError ? (
                    <p className="app-supporting-copy" data-testid="rankings-refresh-error">
                      {refreshError}
                    </p>
                  ) : null}
                </>
              ) : null}

              {data && data.items.length > 0 ? (
                <TableContainer
                  title={tableTitle(data, t("rankings.title"), t)}
                  className="app-portfolio-table-wrap"
                >
                  <Table size="lg" useZebraStyles>
                    <TableHead>
                      <TableRow>
                        <TableHeader>{t("rankings.column.rank")}</TableHeader>
                        <TableHeader>{t("rankings.column.symbol")}</TableHeader>
                        {market === "TW" ? <TableHeader>{t("rankings.column.board")}</TableHeader> : null}
                        <TableHeader>{t("rankings.column.name")}</TableHeader>
                        <TableHeader>{t("rankings.column.lastPrice")}</TableHeader>
                        <TableHeader>{t("rankings.column.changePercent")}</TableHeader>
                        <TableHeader>{t("rankings.column.volume")}</TableHeader>
                        {showNetBuySellColumn ? <TableHeader>{t("rankings.column.netBuySell")}</TableHeader> : null}
                        {showValuationColumns ? <TableHeader>{t("rankings.column.peRatio")}</TableHeader> : null}
                        {showValuationColumns ? <TableHeader>{t("rankings.column.peRatioSource")}</TableHeader> : null}
                        {showValuationColumns ? <TableHeader>{t("rankings.column.valuationAsOf")}</TableHeader> : null}
                        <TableHeader>{t("rankings.column.dataTime")}</TableHeader>
                        <TableHeader>{t("rankings.column.source")}</TableHeader>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {data.items.map((item) => {
                        const detailHref = `/stock/${encodeURIComponent(item.stock.id)}`;
                        return (
                        <TableRow
                          className="app-rankings-row-link"
                          data-testid={`rankings-row-${item.stock.id}`}
                          key={item.stock.id}
                          onClick={(event) => {
                            if (shouldIgnoreRankingsNavigation(event.target)) {
                              return;
                            }
                            router.push(detailHref);
                          }}
                        >
                          <TableCell>{item.rank}</TableCell>
                          <TableCell>
                            <Link className="app-rankings-symbol-link" href={detailHref}>
                              <span className="app-result-symbol">{item.stock.symbol}</span>
                            </Link>
                          </TableCell>
                          {market === "TW" ? <TableCell>{item.stock.exchange}</TableCell> : null}
                          <TableCell>
                            <Link className="app-rankings-name-link" href={detailHref}>
                              {getDisplayStockName(item.stock, language)}
                            </Link>
                          </TableCell>
                          <TableCell>
                            {formatMaybeCurrency(item.lastPrice, item.stock.currency)}
                          </TableCell>
                          <TableCell>
                            {item.changePercent == null ? (
                              "—"
                            ) : (
                              <span
                                className={priceToneClassForMarket(item.stock.market, item.changePercent)}
                                data-testid={`rankings-change-percent-${item.stock.id}`}
                              >
                                {formatMaybePercent(item.changePercent)}
                              </span>
                            )}
                          </TableCell>
                          <TableCell>{formatMaybeNumber(item.volume, formatNumber)}</TableCell>
                          {showNetBuySellColumn ? (
                            <TableCell>{formatMaybeNumber(item.netBuySellShares, formatNumber)}</TableCell>
                          ) : null}
                          {showValuationColumns ? (
                            <TableCell>{formatMaybePeRatio(item.peRatio, language)}</TableCell>
                          ) : null}
                          {showValuationColumns ? (
                            <TableCell>{formatMaybeText(item.peRatioSource)}</TableCell>
                          ) : null}
                          {showValuationColumns ? (
                            <TableCell>{formatMaybeText(item.valuationAsOf)}</TableCell>
                          ) : null}
                          <TableCell>{formatLastUpdated(item.timestamp)}</TableCell>
                          <TableCell>{localizedSource}</TableCell>
                        </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </TableContainer>
              ) : null}

              {data && data.items.length === 0 && !showUnsupported ? (
                <p className="app-supporting-copy">{t("rankings.empty")}</p>
              ) : null}
            </>
          )}
        </Tile>
      </Column>
    </Grid>
  );
}

function shouldIgnoreRankingsNavigation(target: EventTarget | null) {
  return target instanceof HTMLElement
    ? Boolean(target.closest("a, button, input, select, textarea, summary, [role='button']"))
    : false;
}
