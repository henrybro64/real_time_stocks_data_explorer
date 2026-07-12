"use client";

import { InlineLoading } from "@carbon/react";
import type { TaiwanInstitutionalInvestorsResponse } from "@shared/api";
import { useQuery } from "@tanstack/react-query";
import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { toApiError, fetchTaiwanInstitutionalInvestors } from "../../services/api/client";
import { InstitutionalHistogramChart } from "./InstitutionalHistogramChart";
import {
  buildDateRangeLabel,
  formatDisplayDate,
  formatSignedMetric,
  twMarketToneClass,
} from "./twSignalsFormat";

type Props = {
  stockId: string;
};

const HISTORY_DAYS = 90;

export function InstitutionalInvestorsPanel({ stockId }: Readonly<Props>) {
  const { language, t, formatNumber } = useLanguage();
  const copy = language === "zh-TW"
    ? {
        chartEmpty: "此區間沒有可繪製的法人資料。",
        chartForeign: "外資買賣超(張)",
        chartInvestmentTrust: "投信買賣超(張)",
        chartDealer: "自營商買賣超(張)",
        summaryTitle: "三大法人買賣超",
        empty: "目前沒有可顯示的三大法人資料。",
        foreign: "外資",
        investmentTrust: "投信",
        loading: "載入法人動態中",
        lotsUnit: "張",
        records: (count: number) => `近 ${count} 筆交易日資料`,
        source: (source: string) => `來源：${source}`,
        total: "合計",
        dealer: "自營商",
      }
    : {
        chartEmpty: "No plottable institutional history for this range.",
        chartForeign: "Foreign net lots",
        chartInvestmentTrust: "Investment trust net lots",
        chartDealer: "Dealer net lots",
        summaryTitle: "Institutional net activity",
        empty: "No institutional activity is available for this stock yet.",
        foreign: "Foreign",
        investmentTrust: "Investment trust",
        loading: "Loading institutional activity",
        lotsUnit: "lots",
        records: (count: number) => `Latest ${count} trading records`,
        source: (source: string) => `Source: ${source}`,
        total: "Total",
        dealer: "Dealer",
      };

  const institutionalQuery = useQuery<TaiwanInstitutionalInvestorsResponse>({
    queryKey: ["institutional-investors", stockId, HISTORY_DAYS],
    queryFn: () => fetchTaiwanInstitutionalInvestors(stockId, HISTORY_DAYS),
    enabled: stockId.length > 0,
    staleTime: 60_000,
  });

  const payload = institutionalQuery.data;
  const history = payload?.history ?? [];
  const summary = payload?.summary ?? null;
  const latestDate = formatDisplayDate(payload?.latestDate ?? null, language);
  const dateRangeLabel = buildDateRangeLabel(history, language);
  const apiError = institutionalQuery.error ? toApiError(institutionalQuery.error) : null;
  // Any 404 from this TW-only endpoint means the stock/market is unsupported for this feed.
  const shouldShowUnavailableCopy =
    summary == null && history.length === 0 && apiError?.kind === "not_found";

  if (!payload && institutionalQuery.isLoading) {
    return <InlineLoading description={copy.loading} status="active" />;
  }

  if (!payload && shouldShowUnavailableCopy) {
    return <p className="app-supporting-copy">{copy.empty}</p>;
  }

  if (!payload && institutionalQuery.isError) {
    return (
      <RequestStateNotice
        kind={errorToNoticeKind(institutionalQuery.error)}
        scopeLabel={t("detail.twSignals.institutionalTitle")}
      />
    );
  }

  if (!payload || !summary || history.length === 0) {
    return <p className="app-supporting-copy">{copy.empty}</p>;
  }

  const summaryRows = [
    { label: copy.foreign, value: summary.foreignNetLots },
    { label: copy.investmentTrust, value: summary.investmentTrustNetLots },
    { label: copy.dealer, value: summary.dealerNetLots },
    { label: copy.total, value: summary.totalNetLots },
  ];

  return (
    <div className="app-section-stack">
      {payload && institutionalQuery.isError ? (
        <RequestStateNotice
          compact
          kind="stale"
          scopeLabel={t("detail.twSignals.institutionalTitle")}
        />
      ) : null}
      <section className="app-subtile app-tw-inst-summary-card" data-testid="tw-institutional-summary">
        <div className="app-tw-inst-summary-head">
          <h3 className="app-tw-inst-summary-title">{copy.summaryTitle}</h3>
          <p className="app-tw-inst-summary-date">{latestDate}</p>
        </div>
        <dl className="app-tw-inst-summary-list">
          {summaryRows.map((row) => (
            <div className="app-tw-inst-summary-row" key={row.label}>
              <dt>{row.label}</dt>
              <dd className={twMarketToneClass(row.value)}>
                {formatSignedMetric(row.value, formatNumber, copy.lotsUnit)}
              </dd>
            </div>
          ))}
        </dl>
        <div className="app-tw-inst-summary-meta">
          <p className="app-section-meta">{copy.records(history.length)}</p>
          <p className="app-section-meta">{copy.source(payload.source)}</p>
        </div>
      </section>

      <InstitutionalHistogramChart
        dateRangeLabel={dateRangeLabel}
        emptyLabel={copy.chartEmpty}
        language={language}
        points={history.map((point) => ({ date: point.date, value: point.foreignNetLots }))}
        title={copy.chartForeign}
      />
      <InstitutionalHistogramChart
        dateRangeLabel={dateRangeLabel}
        emptyLabel={copy.chartEmpty}
        language={language}
        points={history.map((point) => ({ date: point.date, value: point.investmentTrustNetLots }))}
        title={copy.chartInvestmentTrust}
      />
      <InstitutionalHistogramChart
        dateRangeLabel={dateRangeLabel}
        emptyLabel={copy.chartEmpty}
        language={language}
        points={history.map((point) => ({ date: point.date, value: point.dealerNetLots }))}
        title={copy.chartDealer}
      />
    </div>
  );
}
