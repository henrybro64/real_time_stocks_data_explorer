"use client";

import { Button, ButtonSet, InlineLoading } from "@carbon/react";
import type {
  OwnershipComponentState,
  OwnershipDataStatus,
  TaiwanOwnershipConcentrationResponse,
} from "@shared/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { fetchTaiwanOwnershipConcentration, toApiError } from "../../services/api/client";
import { TaiwanMultiSeriesLineChart } from "./TaiwanMultiSeriesLineChart";
import { formatDisplayDate, formatUnsignedMetric } from "./twSignalsFormat";

type Props = {
  active?: boolean;
  stockId: string;
};

type OwnershipRange = "3M" | "6M" | "1Y";

const RANGES: OwnershipRange[] = ["3M", "6M", "1Y"];

const SERIES_COLORS = {
  holders: "#78a9ff",
  foreign: "#42be65",
  weeklyClose: "#8a3ffc",
} as const;

type OwnershipCopy = {
  chartEmpty: string;
  chartTitle: string;
  empty: string;
  foreign: string;
  holders: string;
  loading: string;
  records: (count: number) => string;
  source: (source: string) => string;
  summaryTitle: string;
  weeklyClose: string;
  partialNotice: string;
  investmentTrustNote: string;
  institutionalPointer: string;
  marginPointer: string;
  foreignTemporarilyUnavailable: string;
  foreignNotPublished: string;
  foreignNoData: string;
  weeklyCloseTemporarilyUnavailable: string;
  weeklyCloseNoData: string;
  holdersUnavailable: string;
  missingValue: string;
};

function ownershipCopy(language: "en" | "zh-TW"): OwnershipCopy {
  if (language === "zh-TW") {
    return {
      chartEmpty: "此區間沒有可繪製的籌碼集中度資料。",
      chartTitle: "持股集中度、外資持股與週收盤",
      empty: "目前沒有可顯示的籌碼集中度資料。",
      foreign: "外資持股(%)",
      holders: "400張以上持股(%)",
      loading: "載入籌碼集中度中",
      records: (count: number) => `近 ${count} 筆資料`,
      source: (source: string) => `來源：${source}`,
      summaryTitle: "籌碼集中度",
      weeklyClose: "週收盤",
      partialNotice: "部分資料暫時無法取得，以下為目前可顯示的內容。",
      investmentTrustNote: "目前的官方資料來源不提供投信持股比例。",
      institutionalPointer: "外資與投信買賣超請至「法人動態」查看。",
      marginPointer: "融資與融券資料請至「資券變化」查看。",
      foreignTemporarilyUnavailable: "外資持股資料暫時無法取得，請稍後再試。",
      foreignNotPublished: "目前的官方資料來源不提供此外資持股比例。",
      foreignNoData: "此區間沒有外資持股資料。",
      weeklyCloseTemporarilyUnavailable: "週收盤價載入失敗。",
      weeklyCloseNoData: "此區間沒有週收盤價。",
      holdersUnavailable: "400張以上持股資料暫時無法取得。",
      missingValue: "—",
    };
  }
  return {
    chartEmpty: "No plottable ownership concentration history for this range.",
    chartTitle: "Concentration, foreign ownership, and weekly close",
    empty: "No ownership concentration data is available for this stock yet.",
    foreign: "Foreign ownership (%)",
    holders: "Holders ≥400 lots (%)",
    loading: "Loading ownership concentration",
    records: (count: number) => `Latest ${count} records`,
    source: (source: string) => `Source: ${source}`,
    summaryTitle: "Ownership concentration",
    weeklyClose: "Weekly close",
    partialNotice: "Some data is temporarily unavailable. Showing what is available.",
    investmentTrustNote:
      "The current official sources do not publish investment-trust ownership percentages.",
    institutionalPointer: "See Institutional Activity for foreign and investment-trust net buying.",
    marginPointer: "See Margin & Short Selling for margin financing data.",
    foreignTemporarilyUnavailable:
      "Foreign ownership data is temporarily unavailable. Please try again later.",
    foreignNotPublished: "Official sources do not publish foreign ownership for this security.",
    foreignNoData: "No foreign ownership data for this period.",
    weeklyCloseTemporarilyUnavailable: "Weekly closing prices could not be loaded.",
    weeklyCloseNoData: "No weekly closing prices for this period.",
    holdersUnavailable: "Holders ≥400 lots data is temporarily unavailable.",
    missingValue: "n/a",
  };
}

function componentMessage(
  state: OwnershipComponentState | undefined,
  copy: OwnershipCopy,
  kind: "foreign" | "weeklyClose" | "holders",
): string | null {
  if (state == null) {
    return null;
  }
  if (state.status === "available") {
    return null;
  }
  if (kind === "foreign") {
    if (state.status === "temporarily_unavailable") {
      return copy.foreignTemporarilyUnavailable;
    }
    if (state.status === "not_published" || state.status === "unsupported") {
      return copy.foreignNotPublished;
    }
    return copy.foreignNoData;
  }
  if (kind === "weeklyClose") {
    if (state.status === "temporarily_unavailable") {
      return copy.weeklyCloseTemporarilyUnavailable;
    }
    return copy.weeklyCloseNoData;
  }
  if (state.status === "temporarily_unavailable") {
    return copy.holdersUnavailable;
  }
  return null;
}

function hasTemporarilyUnavailable(status: OwnershipDataStatus | undefined): boolean {
  if (status == null) {
    return false;
  }
  const components = [status.tdcc, status.foreignOwnership, status.weeklyClose];
  return components.some(
    (component) =>
      component.status === "temporarily_unavailable" ||
      component.reason === "partial_upstream_error",
  );
}

function formatSummaryValue(
  value: number | null | undefined,
  formatNumber: (value: number) => string,
  unit: string,
  fallbackDash: string,
): string {
  if (value != null && Number.isFinite(value)) {
    return formatUnsignedMetric(value, formatNumber, unit);
  }
  return fallbackDash;
}

export function OwnershipConcentrationPanel({ active = true, stockId }: Readonly<Props>) {
  const { language, t, formatNumber } = useLanguage();
  const [range, setRange] = useState<OwnershipRange>("1Y");
  const copy = ownershipCopy(language);

  const ownershipQuery = useQuery<TaiwanOwnershipConcentrationResponse>({
    queryKey: ["tw-ownership-concentration", stockId, range],
    queryFn: () => fetchTaiwanOwnershipConcentration(stockId, range),
    enabled: active && stockId.length > 0,
    staleTime: 60_000,
  });

  const payload = ownershipQuery.data;
  const points = payload?.points ?? [];
  const latest = points[points.length - 1] ?? null;
  const latestDate = formatDisplayDate(payload?.asOf ?? null, language);
  const apiError = ownershipQuery.error ? toApiError(ownershipQuery.error) : null;
  const shouldShowUnavailableCopy = points.length === 0 && apiError?.kind === "not_found";

  return (
    <div className="app-section-stack" data-testid="tw-ownership-concentration-panel">
      <fieldset className="app-tw-range-controls" aria-label={t("detail.twSignals.ownershipTitle")}>
        <ButtonSet>
          {RANGES.map((item) => (
            <Button
              key={item}
              kind={item === range ? "primary" : "tertiary"}
              onClick={() => setRange(item)}
              size="sm"
              type="button"
            >
              {item}
            </Button>
          ))}
        </ButtonSet>
      </fieldset>

      {!payload && ownershipQuery.isLoading ? (
        <InlineLoading description={copy.loading} status="active" />
      ) : null}

      {!payload && shouldShowUnavailableCopy ? (
        <p className="app-supporting-copy">{copy.empty}</p>
      ) : null}

      {!payload && ownershipQuery.isError && !shouldShowUnavailableCopy ? (
        <RequestStateNotice
          detail={apiError?.detail ?? null}
          kind={errorToNoticeKind(ownershipQuery.error)}
          scopeLabel={t("detail.twSignals.ownershipTitle")}
        />
      ) : null}

      {payload && points.length === 0 ? <p className="app-supporting-copy">{copy.empty}</p> : null}

      {payload && points.length > 0 && latest ? (
        <OwnershipLoadedBody
          copy={copy}
          formatNumber={formatNumber}
          language={language}
          latest={latest}
          latestDate={latestDate}
          ownershipQueryError={ownershipQuery.isError}
          payload={payload}
          points={points}
          scopeLabel={t("detail.twSignals.ownershipTitle")}
        />
      ) : null}
    </div>
  );
}

function OwnershipLoadedBody({
  copy,
  formatNumber,
  language,
  latest,
  latestDate,
  ownershipQueryError,
  payload,
  points,
  scopeLabel,
}: Readonly<{
  copy: OwnershipCopy;
  formatNumber: (value: number) => string;
  language: "en" | "zh-TW";
  latest: TaiwanOwnershipConcentrationResponse["points"][number];
  latestDate: string;
  ownershipQueryError: boolean;
  payload: TaiwanOwnershipConcentrationResponse;
  points: TaiwanOwnershipConcentrationResponse["points"];
  scopeLabel: string;
}>) {
  const currency = payload.currency?.trim() || "TWD";
  const dataStatus = payload.dataStatus;
  const foreignMessage = componentMessage(dataStatus?.foreignOwnership, copy, "foreign");
  const weeklyCloseMessage = componentMessage(dataStatus?.weeklyClose, copy, "weeklyClose");
  const holdersMessage = componentMessage(dataStatus?.tdcc, copy, "holders");
  const showPartial =
    hasTemporarilyUnavailable(dataStatus) || payload.stale || ownershipQueryError;

  return (
    <>
      {payload.stale || ownershipQueryError ? (
        <RequestStateNotice compact kind="stale" scopeLabel={scopeLabel} />
      ) : null}
      {showPartial && !payload.stale && !ownershipQueryError ? (
        <p className="app-supporting-copy" data-testid="tw-ownership-partial-notice">
          {copy.partialNotice}
        </p>
      ) : null}
      <section className="app-subtile app-tw-inst-summary-card" data-testid="tw-ownership-summary">
        <div className="app-tw-inst-summary-head">
          <h3 className="app-tw-inst-summary-title">{copy.summaryTitle}</h3>
          <p className="app-tw-inst-summary-date">{latestDate}</p>
        </div>
        <dl className="app-tw-inst-summary-list">
          <div className="app-tw-inst-summary-row">
            <dt>{copy.holders}</dt>
            <dd className="app-tw-market-neutral">
              {formatSummaryValue(
                latest.holders400LotsOrMorePercent,
                formatNumber,
                "%",
                copy.missingValue,
              )}
            </dd>
          </div>
          <div className="app-tw-inst-summary-row">
            <dt>{copy.foreign}</dt>
            <dd className="app-tw-market-neutral" data-testid="tw-ownership-foreign-value">
              {formatSummaryValue(
                latest.foreignOwnershipPercent,
                formatNumber,
                "%",
                copy.missingValue,
              )}
            </dd>
          </div>
          <div className="app-tw-inst-summary-row">
            <dt>{copy.weeklyClose}</dt>
            <dd className="app-tw-market-neutral" data-testid="tw-ownership-weekly-close-value">
              {formatSummaryValue(
                latest.weeklyClose,
                formatNumber,
                currency,
                copy.missingValue,
              )}
            </dd>
          </div>
        </dl>
        <div className="app-tw-ownership-notes" data-testid="tw-ownership-notes">
          {holdersMessage ? (
            <p className="app-section-meta" data-testid="tw-ownership-holders-status">
              {holdersMessage}
            </p>
          ) : null}
          {foreignMessage ? (
            <p className="app-section-meta" data-testid="tw-ownership-foreign-status">
              {foreignMessage}
            </p>
          ) : null}
          {weeklyCloseMessage ? (
            <p className="app-section-meta" data-testid="tw-ownership-weekly-close-status">
              {weeklyCloseMessage}
            </p>
          ) : null}
          <p className="app-section-meta">{copy.investmentTrustNote}</p>
          <p className="app-section-meta">{copy.institutionalPointer}</p>
          <p className="app-section-meta">{copy.marginPointer}</p>
        </div>
        <div className="app-tw-inst-summary-meta">
          <p className="app-section-meta">{copy.records(points.length)}</p>
          <p className="app-section-meta">{copy.source(payload.source)}</p>
        </div>
      </section>

      <TaiwanMultiSeriesLineChart
        dateRangeLabel={`${formatDisplayDate(points[0]?.date ?? null, language)} - ${formatDisplayDate(points[points.length - 1]?.date ?? null, language)}`}
        emptyLabel={copy.chartEmpty}
        language={language}
        missingValueLabel={copy.missingValue}
        lines={[
          { key: "holders", label: copy.holders, color: SERIES_COLORS.holders, axis: "left", unit: "%" },
          { key: "foreign", label: copy.foreign, color: SERIES_COLORS.foreign, axis: "left", unit: "%" },
          {
            key: "weeklyClose",
            label: copy.weeklyClose,
            color: SERIES_COLORS.weeklyClose,
            axis: "right",
            unit: currency,
          },
        ]}
        points={points.map((point) => ({
          date: point.date,
          values: {
            holders: point.holders400LotsOrMorePercent,
            foreign: point.foreignOwnershipPercent,
            weeklyClose: point.weeklyClose,
          },
        }))}
        title={copy.chartTitle}
      />
    </>
  );
}
