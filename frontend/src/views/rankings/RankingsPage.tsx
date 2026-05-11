"use client";

import {
  Button,
  Column,
  Grid,
  InlineLoading,
  Select,
  SelectItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tile,
} from "@carbon/react";
import { useQuery } from "@tanstack/react-query";
import type { RankingResponse, RankingType } from "@shared/api";
import { useState } from "react";

import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { fetchRankings } from "../../services/api/client";
import { formatCurrency, formatLastUpdated, formatPercent } from "../../services/format/quote";
import {
  getLocalizedRankingsFreshnessLabel,
  getLocalizedRankingsSource,
  getLocalizedRankingsUnsupportedReason,
  getLocalizedRankingsWarningMessage,
} from "./rankingsCopy";

const DEFAULT_LIMIT = 50;
type RankingsTabLabelKey =
  | "rankings.tab.netBuy"
  | "rankings.tab.netSell"
  | "rankings.tab.volume"
  | "rankings.tab.volumeUp"
  | "rankings.tab.volumeDown";
type RankingsMarketLabelKey = "rankings.market.tw" | "rankings.market.us";

const RANKING_TABS: Array<{ key: RankingType; labelKey: RankingsTabLabelKey }> = [
  { key: "net_buy", labelKey: "rankings.tab.netBuy" },
  { key: "net_sell", labelKey: "rankings.tab.netSell" },
  { key: "volume", labelKey: "rankings.tab.volume" },
  { key: "volume_up", labelKey: "rankings.tab.volumeUp" },
  { key: "volume_down", labelKey: "rankings.tab.volumeDown" },
];
const MARKET_LABEL_KEYS: Record<"TW" | "US", RankingsMarketLabelKey> = {
  TW: "rankings.market.tw",
  US: "rankings.market.us",
};

export function getRankingsRefetchInterval(isMarketOpen: boolean, rankingType: RankingType) {
  if (rankingType === "net_buy" || rankingType === "net_sell") {
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

function tableTitle(
  response: RankingResponse | undefined,
  fallback: string,
  translate: (key: RankingsTabLabelKey | RankingsMarketLabelKey) => string,
) {
  if (!response) {
    return fallback;
  }
  const tab = RANKING_TABS.find((candidate) => candidate.key === response.rankingType);
  const marketLabel = translate(MARKET_LABEL_KEYS[response.market]);
  const rankingLabel = tab ? translate(tab.labelKey) : response.rankingType;
  return `${marketLabel} ${rankingLabel}`;
}

export function RankingsPage() {
  const { language, t, formatNumber } = useLanguage();
  const [market, setMarket] = useState<"US" | "TW">("TW");
  const [rankingType, setRankingType] = useState<RankingType>("volume");

  const rankingsQuery = useQuery<RankingResponse>({
    queryKey: ["rankings", market, rankingType, DEFAULT_LIMIT],
    queryFn: () => fetchRankings(market, rankingType, DEFAULT_LIMIT),
    refetchInterval: (query) =>
      getRankingsRefetchInterval(
        (query.state.data as RankingResponse | undefined)?.isMarketOpen ?? false,
        rankingType,
      ),
  });

  const data = rankingsQuery.data;
  const showError = rankingsQuery.error != null && data == null;
  const showUnsupported = !!data?.unsupportedReason;
  const localizedSource = data ? getLocalizedRankingsSource(data, t) : "";

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
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("rankings.market")}</p>
              <h2>{t("rankings.title")}</h2>
            </div>
          </div>
          <div className="app-portfolio-asset-form">
            <Select
              id="rankings-market-select"
              data-testid="rankings-market-select"
              labelText={t("rankings.market")}
              value={market}
              onChange={(event) => setMarket(event.currentTarget.value as "US" | "TW")}
            >
              <SelectItem value="TW" text={t("rankings.market.tw")} />
              <SelectItem value="US" text={t("rankings.market.us")} />
            </Select>
          </div>
          <div className="app-shell-brand" style={{ marginTop: "1rem" }}>
            <div className="app-auth-actions" style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
              {RANKING_TABS.map((tab) => (
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
            <RequestStateNotice kind={errorToNoticeKind(rankingsQuery.error)} scopeLabel={t("rankings.title")} />
          ) : rankingsQuery.isLoading && data == null ? (
            <InlineLoading description={t("rankings.loading")} status="active" />
          ) : (
            <>
              {data ? (
                <>
                  <p className="app-section-meta" data-testid="rankings-freshness-label">
                    {getLocalizedRankingsFreshnessLabel(data, t)}
                  </p>
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
                        <TableHeader>{t("rankings.column.name")}</TableHeader>
                        <TableHeader>{t("rankings.column.lastPrice")}</TableHeader>
                        <TableHeader>{t("rankings.column.changePercent")}</TableHeader>
                        <TableHeader>{t("rankings.column.volume")}</TableHeader>
                        <TableHeader>{t("rankings.column.volumeChangePercent")}</TableHeader>
                        <TableHeader>{t("rankings.column.netBuySell")}</TableHeader>
                        <TableHeader>{t("rankings.column.dataTime")}</TableHeader>
                        <TableHeader>{t("rankings.column.source")}</TableHeader>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {data.items.map((item) => (
                        <TableRow data-testid={`rankings-row-${item.stock.id}`} key={item.stock.id}>
                          <TableCell>{item.rank}</TableCell>
                          <TableCell>
                            <span className="app-result-symbol">{item.stock.symbol}</span>
                          </TableCell>
                          <TableCell>{getDisplayStockName(item.stock, language)}</TableCell>
                          <TableCell>
                            {formatMaybeCurrency(item.lastPrice, item.stock.currency)}
                          </TableCell>
                          <TableCell>{formatMaybePercent(item.changePercent)}</TableCell>
                          <TableCell>{formatMaybeNumber(item.volume, formatNumber)}</TableCell>
                          <TableCell>{formatMaybePercent(item.volumeDeltaPercent)}</TableCell>
                          <TableCell>{formatMaybeNumber(item.netBuySellShares, formatNumber)}</TableCell>
                          <TableCell>{formatLastUpdated(item.timestamp)}</TableCell>
                          <TableCell>{localizedSource}</TableCell>
                        </TableRow>
                      ))}
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
