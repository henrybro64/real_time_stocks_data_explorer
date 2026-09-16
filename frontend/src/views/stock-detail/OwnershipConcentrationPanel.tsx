"use client";

import { Button, ButtonSet, InlineLoading } from "@carbon/react";
import type { TaiwanOwnershipConcentrationResponse } from "@shared/api";
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
};

function ownershipCopy(language: "en" | "zh-TW"): OwnershipCopy {
  if (language === "zh-TW") {
    return {
      chartEmpty: "此區間沒有可繪製的籌碼集中度資料。",
      chartTitle: "持股集中度與外資持股",
      empty: "目前沒有可顯示的籌碼集中度資料。",
      foreign: "外資持股(%)",
      holders: "400張以上持股(%)",
      loading: "載入籌碼集中度中",
      records: (count: number) => `近 ${count} 筆資料`,
      source: (source: string) => `來源：${source}`,
      summaryTitle: "籌碼集中度",
      weeklyClose: "週收盤",
    };
  }
  return {
    chartEmpty: "No plottable ownership concentration history for this range.",
    chartTitle: "Concentration and foreign ownership",
    empty: "No ownership concentration data is available for this stock yet.",
    foreign: "Foreign ownership (%)",
    holders: "Holders ≥400 lots (%)",
    loading: "Loading ownership concentration",
    records: (count: number) => `Latest ${count} records`,
    source: (source: string) => `Source: ${source}`,
    summaryTitle: "Ownership concentration",
    weeklyClose: "Weekly close",
  };
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
  return (
    <>
      {payload.stale || ownershipQueryError ? (
        <RequestStateNotice compact kind="stale" scopeLabel={scopeLabel} />
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
              {formatUnsignedMetric(latest.holders400LotsOrMorePercent, formatNumber, "%")}
            </dd>
          </div>
          <div className="app-tw-inst-summary-row">
            <dt>{copy.foreign}</dt>
            <dd className="app-tw-market-neutral">
              {formatUnsignedMetric(latest.foreignOwnershipPercent, formatNumber, "%")}
            </dd>
          </div>
          <div className="app-tw-inst-summary-row">
            <dt>{copy.weeklyClose}</dt>
            <dd className="app-tw-market-neutral">
              {formatUnsignedMetric(latest.weeklyClose, formatNumber, "TWD")}
            </dd>
          </div>
        </dl>
        <div className="app-tw-inst-summary-meta">
          <p className="app-section-meta">{copy.records(points.length)}</p>
          <p className="app-section-meta">{copy.source(payload.source)}</p>
        </div>
      </section>

      <TaiwanMultiSeriesLineChart
        dateRangeLabel={`${formatDisplayDate(points[0]?.date ?? null, language)} - ${formatDisplayDate(points[points.length - 1]?.date ?? null, language)}`}
        emptyLabel={copy.chartEmpty}
        language={language}
        lines={[
          { key: "holders", label: copy.holders, color: SERIES_COLORS.holders },
          { key: "foreign", label: copy.foreign, color: SERIES_COLORS.foreign },
        ]}
        points={points.map((point) => ({
          date: point.date,
          values: {
            holders: point.holders400LotsOrMorePercent,
            foreign: point.foreignOwnershipPercent,
          },
        }))}
        title={copy.chartTitle}
      />
    </>
  );
}
