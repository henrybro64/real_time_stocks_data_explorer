"use client";

import { InlineLoading } from "@carbon/react";
import type { TaiwanMarginShortResponse } from "@shared/api";
import { useQuery } from "@tanstack/react-query";
import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { fetchTaiwanMarginShort, toApiError } from "../../services/api/client";
import { InstitutionalHistogramChart } from "./InstitutionalHistogramChart";
import { TaiwanSignalLineChart } from "./TaiwanSignalLineChart";

type Props = {
  stockId: string;
};

const HISTORY_DAYS = 90;

export function MarginShortPanel({ stockId }: Readonly<Props>) {
  const { language, t, formatNumber } = useLanguage();
  const copy = language === "zh-TW"
    ? {
        amountChange: "借券賣出餘額增減金額(萬)",
        amountUnit: "萬",
        chartEmpty: "此區間沒有可繪製的資券資料。",
        empty: "目前沒有可顯示的資券資料。",
        loading: "載入資券變化中",
        lotsChange: "借券賣出餘額增減張數",
        lotsUnit: "張",
        marginBalance: "融資餘額(張)",
        records: (count: number) => `近 ${count} 筆交易日資料`,
        shortMarginBalance: "融券餘額(張)",
        source: (source: string) => `來源：${source}`,
        summaryTitle: "資券變化",
      }
    : {
        amountChange: "Borrowed short balance change amount (10k TWD)",
        amountUnit: "10k TWD",
        chartEmpty: "No plottable margin / short history for this range.",
        empty: "No margin / short data is available for this stock yet.",
        loading: "Loading margin / short changes",
        lotsChange: "Borrowed short balance change (lots)",
        lotsUnit: "lots",
        marginBalance: "Margin balance (lots)",
        records: (count: number) => `Latest ${count} trading records`,
        shortMarginBalance: "Short margin balance (lots)",
        source: (source: string) => `Source: ${source}`,
        summaryTitle: "Margin / short changes",
      };

  const marginShortQuery = useQuery<TaiwanMarginShortResponse>({
    queryKey: ["margin-short", stockId, HISTORY_DAYS],
    queryFn: () => fetchTaiwanMarginShort(stockId, HISTORY_DAYS),
    enabled: stockId.length > 0,
    staleTime: 60_000,
  });

  const payload = marginShortQuery.data;
  const history = payload?.history ?? [];
  const summary = payload?.summary ?? null;
  const latestDate = formatDisplayDate(payload?.latestDate ?? null, language);
  const dateRangeLabel = buildDateRangeLabel(history, language);
  const apiError = marginShortQuery.error ? toApiError(marginShortQuery.error) : null;
  const shouldShowUnavailableCopy =
    summary == null && history.length === 0 && apiError?.kind === "not_found";

  if (!payload && marginShortQuery.isLoading) {
    return <InlineLoading description={copy.loading} status="active" />;
  }

  if (!payload && shouldShowUnavailableCopy) {
    return <p className="app-supporting-copy">{copy.empty}</p>;
  }

  if (!payload && marginShortQuery.isError) {
    return (
      <RequestStateNotice
        kind={errorToNoticeKind(marginShortQuery.error)}
        scopeLabel={t("detail.twSignals.marginShortTitle")}
      />
    );
  }

  if (!payload || !summary || history.length === 0) {
    return <p className="app-supporting-copy">{copy.empty}</p>;
  }

  const summaryRows = [
    { label: copy.marginBalance, value: summary.marginBalanceLots, signed: false, unit: copy.lotsUnit },
    { label: copy.shortMarginBalance, value: summary.shortMarginBalanceLots, signed: false, unit: copy.lotsUnit },
    { label: copy.lotsChange, value: summary.borrowedShortBalanceChangeLots, signed: true, unit: copy.lotsUnit },
    { label: copy.amountChange, value: summary.borrowedShortBalanceChangeAmount10k, signed: true, unit: copy.amountUnit },
  ];

  return (
    <div className="app-section-stack">
      {payload && marginShortQuery.isError ? (
        <RequestStateNotice
          compact
          kind="stale"
          scopeLabel={t("detail.twSignals.marginShortTitle")}
        />
      ) : null}
      <section className="app-subtile app-tw-inst-summary-card" data-testid="tw-margin-short-summary">
        <div className="app-tw-inst-summary-head">
          <h3 className="app-tw-inst-summary-title">{copy.summaryTitle}</h3>
          <p className="app-tw-inst-summary-date">{latestDate}</p>
        </div>
        <dl className="app-tw-inst-summary-list">
          {summaryRows.map((row) => (
            <div className="app-tw-inst-summary-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd className={row.signed ? twMarketToneClass(row.value) : "app-tw-market-neutral"}>
                {row.signed
                  ? formatSignedMetric(row.value, formatNumber, row.unit)
                  : formatUnsignedMetric(row.value, formatNumber, row.unit)}
              </dd>
            </div>
          ))}
        </dl>
        <div className="app-tw-inst-summary-meta">
          <p className="app-section-meta">{copy.records(history.length)}</p>
          <p className="app-section-meta">{copy.source(payload.source)}</p>
        </div>
      </section>

      <TaiwanSignalLineChart
        dateRangeLabel={dateRangeLabel}
        emptyLabel={copy.chartEmpty}
        language={language}
        points={history.map((point) => ({ date: point.date, value: point.marginBalanceLots }))}
        title={copy.marginBalance}
      />
      <TaiwanSignalLineChart
        dateRangeLabel={dateRangeLabel}
        emptyLabel={copy.chartEmpty}
        language={language}
        points={history.map((point) => ({ date: point.date, value: point.shortMarginBalanceLots }))}
        title={copy.shortMarginBalance}
      />
      <InstitutionalHistogramChart
        dateRangeLabel={dateRangeLabel}
        emptyLabel={copy.chartEmpty}
        language={language}
        points={history.map((point) => ({
          date: point.date,
          value: point.borrowedShortBalanceChangeLots ?? Number.NaN,
        }))}
        title={copy.lotsChange}
      />
      <InstitutionalHistogramChart
        dateRangeLabel={dateRangeLabel}
        emptyLabel={copy.chartEmpty}
        language={language}
        points={history.map((point) => ({
          date: point.date,
          value: point.borrowedShortBalanceChangeAmount10k ?? Number.NaN,
        }))}
        title={copy.amountChange}
      />
    </div>
  );
}

function buildDateRangeLabel(
  history: TaiwanMarginShortResponse["history"],
  language: "en" | "zh-TW",
): string {
  if (history.length === 0) {
    return "";
  }
  const start = formatDisplayDate(history[0]?.date ?? null, language);
  const end = formatDisplayDate(history[history.length - 1]?.date ?? null, language);
  return `${start} - ${end}`;
}

function formatDisplayDate(rawDate: string | null, language: "en" | "zh-TW"): string {
  if (!rawDate) {
    return "—";
  }
  const date = new Date(`${rawDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return rawDate;
  }
  return new Intl.DateTimeFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function formatSignedMetric(
  value: number | null | undefined,
  formatNumber: (value: number) => string,
  unit: string,
): string {
  if (value == null) {
    return "—";
  }
  const absoluteValue = formatLocalizedMetric(Math.abs(value), formatNumber);
  if (value > 0) {
    return `+${absoluteValue} ${unit}`;
  }
  if (value < 0) {
    return `-${absoluteValue} ${unit}`;
  }
  return `${absoluteValue} ${unit}`;
}

function formatUnsignedMetric(
  value: number | null | undefined,
  formatNumber: (value: number) => string,
  unit: string,
): string {
  if (value == null) {
    return "—";
  }
  return `${formatLocalizedMetric(value, formatNumber)} ${unit}`;
}

function formatLocalizedMetric(value: number, formatNumber: (value: number) => string): string {
  if (Number.isInteger(value)) {
    return formatNumber(value);
  }
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 3,
  }).format(value);
}

function twMarketToneClass(value: number | null | undefined): string {
  if (value == null) {
    return "app-tw-market-neutral";
  }
  if (value > 0) {
    return "app-tw-market-positive";
  }
  if (value < 0) {
    return "app-tw-market-negative";
  }
  return "app-tw-market-neutral";
}
