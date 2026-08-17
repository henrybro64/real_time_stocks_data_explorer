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
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { QuoteFreshnessTag } from "../../components/state/QuoteFreshnessTag";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import {
  fetchMarkets,
  fetchRankings,
  refreshRankings,
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
const RANKINGS_REFRESH_COOLDOWN_MS = 15_000;
type RankingMarket = RankingResponse["market"];
type TwBoardSegment = "TWSE" | "TPEx";
type UsUniverseSegment = "DJIA" | "NASDAQ100";

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
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshCooldownUntil, setRefreshCooldownUntil] = useState(0);
  const rankingTabs = tabsForMarket(market);
  const selectedScope: RankingUniverse = market === "TW" ? twBoard : usUniverse;
  const rankingsQueryKey = ["rankings", market, selectedScope, rankingType, DEFAULT_LIMIT] as const;

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

  const rankingsQuery = useQuery<RankingResponse>({
    queryKey: rankingsQueryKey,
    queryFn: ({ signal }) =>
      fetchRankings(
        market,
        rankingType,
        DEFAULT_LIMIT,
        selectedScope,
        signal,
      ),
    retry: shouldRetryRankingsQuery,
    refetchInterval: (query) =>
      getRankingsRefetchInterval(
        (query.state.data as RankingResponse | undefined)?.isMarketOpen ?? false,
        rankingType,
      ),
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshRankings(market, rankingType, DEFAULT_LIMIT, selectedScope),
    onSuccess: (next) => {
      setRefreshError(null);
      queryClient.setQueryData(rankingsQueryKey, next);
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
  const refreshDisabled = refreshMutation.isPending || Date.now() < refreshCooldownUntil;
  const scopeNoteKey =
    market === "US"
      ? usScopeNoticeKeyFromState(rankingType, data)
      : isInstitutionalRanking(rankingType)
        ? twBoard === "TPEx"
          ? showUnsupported
            ? "rankings.notice.twInstScopeTpexUnavailable"
            : "rankings.notice.twInstScopeTpex"
          : "rankings.notice.twInstScopeTwse"
        : twQuoteScopeNoticeKeyFromState(rankingType, twBoard, data);

  function handleMarketChange(nextMarket: RankingMarket) {
    setMarketOverride(nextMarket);
    setRankingType(defaultRankingTypeForMarket(nextMarket));
    setRefreshError(null);
    if (nextMarket === "TW") {
      setTwBoard("TWSE");
    } else {
      setUsUniverse("DJIA");
    }
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
            {data ? <RankingRealtimeTag response={data} /> : null}
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
            <p className="app-supporting-copy" data-testid="rankings-scope-note">
              {t(scopeNoteKey)}
            </p>
          </div>
          {market === "TW" ? (
            <div className="app-shell-brand" style={{ marginTop: "0.75rem" }}>
              <p className="app-eyebrow" style={{ marginBottom: "0.35rem" }}>
                {t("rankings.twBoard.groupLabel")}
              </p>
              <div className="app-auth-actions" style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
                <Button
                  data-testid="rankings-tw-board-twse"
                  kind={twBoard === "TWSE" ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => {
                    setRefreshError(null);
                    setTwBoard("TWSE");
                  }}
                >
                  {t("rankings.twBoard.twse")}
                </Button>
                <Button
                  data-testid="rankings-tw-board-tpex"
                  kind={twBoard === "TPEx" ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => {
                    setRefreshError(null);
                    setTwBoard("TPEx");
                  }}
                >
                  {t("rankings.twBoard.tpex")}
                </Button>
              </div>
            </div>
          ) : (
            <div className="app-shell-brand" style={{ marginTop: "0.75rem" }}>
              <p className="app-eyebrow" style={{ marginBottom: "0.35rem" }}>
                {t("rankings.usUniverse.groupLabel")}
              </p>
              <div className="app-auth-actions" style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
                <Button
                  data-testid="rankings-us-universe-djia"
                  kind={usUniverse === "DJIA" ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => {
                    setRefreshError(null);
                    setUsUniverse("DJIA");
                  }}
                >
                  {t("rankings.usUniverse.djia")}
                </Button>
                <Button
                  data-testid="rankings-us-universe-nasdaq100"
                  kind={usUniverse === "NASDAQ100" ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => {
                    setRefreshError(null);
                    setUsUniverse("NASDAQ100");
                  }}
                >
                  {t("rankings.usUniverse.nasdaq100")}
                </Button>
              </div>
            </div>
          )}
          <div className="app-shell-brand app-rankings-tabs" style={{ marginTop: "1rem" }}>
            <div className="app-auth-actions" style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
              {rankingTabs.map((tab) => (
                <Button
                  data-testid={`rankings-tab-${tab.key}`}
                  key={tab.key}
                  kind={rankingType === tab.key ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => setRankingType(tab.key)}
                >
                  {t(tab.labelKey)}
                </Button>
              ))}
            </div>
          </div>
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
                    <Button
                      data-testid="rankings-refresh-source"
                      kind="tertiary"
                      size="sm"
                      disabled={refreshDisabled}
                      onClick={() => refreshMutation.mutate()}
                    >
                      {refreshMutation.isPending ? t("rankings.refresh.loading") : t("rankings.refresh.action")}
                    </Button>
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
