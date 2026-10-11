"use client";

import { InlineLoading, Tag, Tile } from "@carbon/react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { CommodityQuote, FxRate } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { fetchUsdTwdFxRate, fetchWtiCrudeOilQuote } from "../../services/api/client";
import { refetchIntervalWhileErroring } from "../../services/api/pollingInterval";
import { formatLastUpdated, formatPercent } from "../../services/format/quote";
import { priceToneClass } from "../../services/market-data/priceTone";
import { RequestStateNotice } from "../state/RequestStateNotice";

const REFRESH_MS = 60_000;
const STALE_MS = 60_000;

function changeToneClass(
  convention: "taiwan" | "us",
  change: number | null | undefined,
): string | undefined {
  if (change == null) {
    return undefined;
  }
  if (change === 0) {
    return "app-price-unchanged";
  }
  return priceToneClass(convention, change);
}

function isFxDisplayUnavailable(fx: FxRate | undefined, isError: boolean): boolean {
  if (isError && fx == null) {
    return true;
  }
  // Synthetic portfolio fallback must not appear as a live market figure.
  return fx?.status === "fallback";
}

function isFxDisplayStale(fx: FxRate | undefined, isError: boolean): boolean {
  if (fx == null || fx.status === "fallback") {
    return false;
  }
  return isError || fx.status === "stale";
}

function isOilDisplayUnavailable(oil: CommodityQuote | undefined, isError: boolean): boolean {
  return isError && oil == null;
}

function isOilDisplayStale(oil: CommodityQuote | undefined, isError: boolean): boolean {
  if (oil == null) {
    return false;
  }
  return isError || oil.status === "stale";
}

type SnapshotCardProps = {
  testId: string;
  label: string;
  primaryValue: string | null;
  change: number | null | undefined;
  changePercent: number | null | undefined;
  changeConvention: "taiwan" | "us";
  timestamp: string | null | undefined;
  source: string | null | undefined;
  isLoading: boolean;
  isStale: boolean;
  isUnavailable: boolean;
  onRetry?: () => void;
  signedNumberFormat: Intl.NumberFormat;
  dateLocale: string;
};

function SnapshotCard({
  testId,
  label,
  primaryValue,
  change,
  changePercent,
  changeConvention,
  timestamp,
  source,
  isLoading,
  isStale,
  isUnavailable,
  onRetry,
  signedNumberFormat,
  dateLocale,
}: SnapshotCardProps) {
  const { t } = useLanguage();
  const toneClass = changeToneClass(changeConvention, change);
  const showChange = change != null && changePercent != null && !isUnavailable;

  return (
    <Tile className="app-subtile" data-testid={testId}>
      <div className="app-market-card-top">
        <strong>{label}</strong>
        {isStale ? (
          <Tag size="sm" type="warm-gray">
            {t("marketSnapshot.stale")}
          </Tag>
        ) : null}
      </div>
      {isLoading && primaryValue == null ? (
        <div className="app-market-snapshot-loading" data-testid={`${testId}-loading`}>
          <InlineLoading description={t("marketSnapshot.loading")} status="active" />
        </div>
      ) : isUnavailable || primaryValue == null ? (
        <>
          <p className="app-stock-card-price">—</p>
          <div className="app-state-notice-wrap">
            <RequestStateNotice compact kind="unavailable" onRetry={onRetry} scopeLabel={label} />
          </div>
        </>
      ) : (
        <>
          <p className="app-stock-card-price" data-testid={`${testId}-value`}>
            {primaryValue}
          </p>
          {showChange ? (
            <p
              className={`${toneClass ?? ""} app-stock-card-change`.trim()}
              data-testid={`${testId}-change`}
            >
              {signedNumberFormat.format(change)} ({formatPercent(changePercent)})
            </p>
          ) : null}
          {isStale ? (
            <div className="app-state-notice-wrap">
              <RequestStateNotice compact kind="stale" onRetry={onRetry} scopeLabel={label} />
            </div>
          ) : null}
          {timestamp ? (
            <p className="app-section-meta">
              {t("marketSnapshot.updated")} {formatLastUpdated(timestamp, dateLocale)}
            </p>
          ) : null}
          {source ? (
            <p className="app-section-meta">
              {t("marketSnapshot.source")}: {source}
            </p>
          ) : null}
        </>
      )}
    </Tile>
  );
}

export function MarketSnapshot() {
  const { language, t } = useLanguage();

  const fxQuery = useQuery<FxRate>({
    queryKey: ["usd-twd-fx-rate"],
    queryFn: fetchUsdTwdFxRate,
    staleTime: STALE_MS,
    refetchInterval: (query) => refetchIntervalWhileErroring(query.state.status, REFRESH_MS),
    refetchIntervalInBackground: false,
    placeholderData: keepPreviousData,
  });

  const oilQuery = useQuery<CommodityQuote>({
    queryKey: ["wti-crude-oil"],
    queryFn: fetchWtiCrudeOilQuote,
    staleTime: STALE_MS,
    refetchInterval: (query) => refetchIntervalWhileErroring(query.state.status, REFRESH_MS),
    refetchIntervalInBackground: false,
    placeholderData: keepPreviousData,
  });

  const numberLocale = language === "zh-TW" ? "zh-TW" : "en-US";
  const signedNumberFormat = new Intl.NumberFormat(numberLocale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "always",
  });
  const rateFormat = new Intl.NumberFormat(numberLocale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
  const oilFormat = new Intl.NumberFormat(numberLocale, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const fx = fxQuery.data;
  const oil = oilQuery.data;
  const fxUnavailable = isFxDisplayUnavailable(fx, fxQuery.isError);
  const oilUnavailable = isOilDisplayUnavailable(oil, oilQuery.isError);
  const fxStale = isFxDisplayStale(fx, fxQuery.isError);
  const oilStale = isOilDisplayStale(oil, oilQuery.isError);

  const fxPrimary =
    !fxUnavailable && fx != null && Number.isFinite(fx.rate)
      ? `${rateFormat.format(fx.rate)} ${t("marketSnapshot.twdUnit")}`
      : null;
  const oilPrimary =
    !oilUnavailable && oil != null && Number.isFinite(oil.price)
      ? `${oilFormat.format(oil.price)} ${t("marketSnapshot.perBarrel")}`
      : null;

  return (
    <Tile className="app-section-tile" data-testid="market-snapshot-section">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{t("marketSnapshot.eyebrow")}</p>
          <h2>{t("marketSnapshot.title")}</h2>
        </div>
      </div>
      <div className="app-market-grid app-market-snapshot-grid">
        <SnapshotCard
          testId="market-snapshot-fx"
          label={t("marketSnapshot.fxLabel")}
          primaryValue={fxPrimary}
          change={fx?.change}
          changePercent={fx?.changePercent}
          changeConvention="taiwan"
          timestamp={fx?.timestamp}
          source={fx?.source ?? null}
          isLoading={fxQuery.isLoading}
          isStale={fxStale}
          isUnavailable={fxUnavailable}
          onRetry={fxQuery.isError ? () => void fxQuery.refetch() : undefined}
          signedNumberFormat={signedNumberFormat}
          dateLocale={numberLocale}
        />
        <SnapshotCard
          testId="market-snapshot-wti"
          label={t("marketSnapshot.oilLabel")}
          primaryValue={oilPrimary}
          change={oil?.change}
          changePercent={oil?.changePercent}
          changeConvention="us"
          timestamp={oil?.timestamp}
          source={oil?.source ?? null}
          isLoading={oilQuery.isLoading}
          isStale={oilStale}
          isUnavailable={oilUnavailable}
          onRetry={oilQuery.isError ? () => void oilQuery.refetch() : undefined}
          signedNumberFormat={signedNumberFormat}
          dateLocale={numberLocale}
        />
      </div>
    </Tile>
  );
}
