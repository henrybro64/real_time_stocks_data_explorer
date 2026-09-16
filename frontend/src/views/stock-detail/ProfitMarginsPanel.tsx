"use client";

import { Button, ButtonSet, InlineLoading } from "@carbon/react";
import type { TaiwanProfitabilityResponse } from "@shared/api";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { fetchTaiwanProfitability, shouldRetryStockDetailQuery, stockDetailRetryDelayMs, toApiError } from "../../services/api/client";
import { TaiwanMultiSeriesLineChart } from "./TaiwanMultiSeriesLineChart";
import { formatDisplayDate, formatUnsignedMetric } from "./twSignalsFormat";

type Props = {
  active?: boolean;
  stockId: string;
};

type Quarters = 8 | 12 | 20;

const QUARTER_OPTIONS: Quarters[] = [8, 12, 20];

const SERIES_COLORS = {
  gross: "#78a9ff",
  operating: "#42be65",
  pretax: "#f1c21b",
  net: "#8a3ffc",
} as const;

type ProfitCopy = {
  chartEmpty: string;
  chartTitle: string;
  empty: string;
  gross: string;
  invalidQuarters: string;
  loading: string;
  net: string;
  operating: string;
  pretax: string;
  records: (count: number) => string;
  source: (source: string) => string;
  summaryTitle: string;
  quartersLabel: (value: number) => string;
};

function profitCopy(language: "en" | "zh-TW"): ProfitCopy {
  if (language === "zh-TW") {
    return {
      chartEmpty: "此區間沒有可繪製的財報三率資料。",
      chartTitle: "毛利率 / 營益率 / 稅前 / 淨利率",
      empty: "目前沒有可顯示的財報三率資料。",
      gross: "毛利率(%)",
      invalidQuarters: "無效的財報期間，請選擇 8Q、12Q 或 20Q。",
      loading: "載入財報三率中",
      net: "淨利率(%)",
      operating: "營業利益率(%)",
      pretax: "稅前淨利率(%)",
      records: (count: number) => `近 ${count} 季資料`,
      source: (source: string) => `來源：${source}`,
      summaryTitle: "財報三率",
      quartersLabel: (value: number) => `${value}Q`,
    };
  }
  return {
    chartEmpty: "No plottable profitability margin history for this range.",
    chartTitle: "Gross / operating / pretax / net margins",
    empty: "No profitability margin data is available for this stock yet.",
    gross: "Gross margin (%)",
    invalidQuarters: "Invalid reporting period. Select 8Q, 12Q, or 20Q.",
    loading: "Loading profitability margins",
    net: "Net margin (%)",
    operating: "Operating margin (%)",
    pretax: "Pretax margin (%)",
    records: (count: number) => `Latest ${count} quarters`,
    source: (source: string) => `Source: ${source}`,
    summaryTitle: "Profitability margins",
    quartersLabel: (value: number) => `${value}Q`,
  };
}

export function ProfitMarginsPanel({ active = true, stockId }: Readonly<Props>) {
  const { language, t, formatNumber } = useLanguage();
  const [quarters, setQuarters] = useState<Quarters>(12);
  const copy = profitCopy(language);

  const profitabilityQuery = useQuery<TaiwanProfitabilityResponse>({
    queryKey: ["tw-profitability", stockId, quarters],
    queryFn: () => fetchTaiwanProfitability(stockId, quarters),
    enabled: active && stockId.length > 0,
    staleTime: 60_000,
    retry: shouldRetryStockDetailQuery,
    retryDelay: (failureCount, error) => stockDetailRetryDelayMs(failureCount, error),
  });

  const payload = profitabilityQuery.data;
  const points = payload?.points ?? [];
  const latest = points[points.length - 1] ?? null;
  const latestDate = formatDisplayDate(payload?.asOf ?? null, language);
  const apiError = profitabilityQuery.error ? toApiError(profitabilityQuery.error) : null;
  const shouldShowUnavailableCopy = points.length === 0 && apiError?.kind === "not_found";

  return (
    <div className="app-section-stack" data-testid="tw-profit-margins-panel">
      <fieldset className="app-tw-range-controls" aria-label={t("detail.twSignals.profitMarginsTitle")}>
        <ButtonSet>
          {QUARTER_OPTIONS.map((item) => (
            <Button
              key={item}
              kind={item === quarters ? "primary" : "tertiary"}
              onClick={() => setQuarters(item)}
              size="sm"
              type="button"
            >
              {copy.quartersLabel(item)}
            </Button>
          ))}
        </ButtonSet>
      </fieldset>

      {!payload && profitabilityQuery.isLoading ? (
        <InlineLoading description={copy.loading} status="active" />
      ) : null}

      {!payload && shouldShowUnavailableCopy ? (
        <p className="app-supporting-copy">{copy.empty}</p>
      ) : null}

      {!payload && profitabilityQuery.isError && !shouldShowUnavailableCopy ? (
        apiError?.kind === "validation" ? (
          <p
            className="app-supporting-copy"
            data-testid="tw-profit-margins-validation"
            role="alert"
          >
            {copy.invalidQuarters}
          </p>
        ) : (
          <RequestStateNotice
            detail={apiError?.detail ?? null}
            kind={errorToNoticeKind(profitabilityQuery.error)}
            scopeLabel={t("detail.twSignals.profitMarginsTitle")}
            status={apiError?.status ?? null}
          />
        )
      ) : null}

      {payload && points.length === 0 ? <p className="app-supporting-copy">{copy.empty}</p> : null}

      {payload && points.length > 0 && latest ? (
        <ProfitLoadedBody
          copy={copy}
          formatNumber={formatNumber}
          language={language}
          latest={latest}
          latestDate={latestDate}
          payload={payload}
          points={points}
          queryError={profitabilityQuery.isError}
          scopeLabel={t("detail.twSignals.profitMarginsTitle")}
        />
      ) : null}
    </div>
  );
}

function ProfitLoadedBody({
  copy,
  formatNumber,
  language,
  latest,
  latestDate,
  payload,
  points,
  queryError,
  scopeLabel,
}: Readonly<{
  copy: ProfitCopy;
  formatNumber: (value: number) => string;
  language: "en" | "zh-TW";
  latest: TaiwanProfitabilityResponse["points"][number];
  latestDate: string;
  payload: TaiwanProfitabilityResponse;
  points: TaiwanProfitabilityResponse["points"];
  queryError: boolean;
  scopeLabel: string;
}>) {
  return (
    <>
      {payload.stale || queryError ? (
        <RequestStateNotice compact kind="stale" scopeLabel={scopeLabel} />
      ) : null}
      <section className="app-subtile app-tw-inst-summary-card" data-testid="tw-profit-margins-summary">
        <div className="app-tw-inst-summary-head">
          <h3 className="app-tw-inst-summary-title">{copy.summaryTitle}</h3>
          <p className="app-tw-inst-summary-date">{latestDate}</p>
        </div>
        <dl className="app-tw-inst-summary-list">
          <div className="app-tw-inst-summary-row">
            <dt>{copy.gross}</dt>
            <dd className="app-tw-market-neutral">
              {formatUnsignedMetric(latest.grossMarginPercent, formatNumber, "%")}
            </dd>
          </div>
          <div className="app-tw-inst-summary-row">
            <dt>{copy.operating}</dt>
            <dd className="app-tw-market-neutral">
              {formatUnsignedMetric(latest.operatingMarginPercent, formatNumber, "%")}
            </dd>
          </div>
          <div className="app-tw-inst-summary-row">
            <dt>{copy.pretax}</dt>
            <dd className="app-tw-market-neutral">
              {formatUnsignedMetric(latest.pretaxMarginPercent, formatNumber, "%")}
            </dd>
          </div>
          <div className="app-tw-inst-summary-row">
            <dt>{copy.net}</dt>
            <dd className="app-tw-market-neutral">
              {formatUnsignedMetric(latest.netMarginPercent, formatNumber, "%")}
            </dd>
          </div>
        </dl>
        <div className="app-tw-inst-summary-meta">
          <p className="app-section-meta">{copy.records(points.length)}</p>
          <p className="app-section-meta">{copy.source(payload.source)}</p>
        </div>
      </section>

      <TaiwanMultiSeriesLineChart
        dateRangeLabel={`${points[0]?.fiscalQuarter ?? ""} - ${points[points.length - 1]?.fiscalQuarter ?? ""}`}
        emptyLabel={copy.chartEmpty}
        formatPointLabel={(point) => point.label ?? point.date}
        language={language}
        lines={[
          { key: "gross", label: copy.gross, color: SERIES_COLORS.gross },
          { key: "operating", label: copy.operating, color: SERIES_COLORS.operating },
          { key: "pretax", label: copy.pretax, color: SERIES_COLORS.pretax },
          { key: "net", label: copy.net, color: SERIES_COLORS.net },
        ]}
        points={points.map((point) => ({
          date: point.periodEnd,
          label: point.fiscalQuarter,
          values: {
            gross: point.grossMarginPercent,
            operating: point.operatingMarginPercent,
            pretax: point.pretaxMarginPercent,
            net: point.netMarginPercent,
          },
        }))}
        title={copy.chartTitle}
      />
    </>
  );
}
