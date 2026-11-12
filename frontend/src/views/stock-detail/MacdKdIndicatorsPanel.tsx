"use client";

import { InlineLoading, Tile } from "@carbon/react";
import { useQuery } from "@tanstack/react-query";

import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import {
  fetchTechnicalIndicators,
  type TechnicalIndicatorsResponse,
  type TechnicalIndicatorSnapshot,
} from "../../services/api/client";

type Props = {
  stockId: string;
};

function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }
  return Number(value).toFixed(digits);
}

function SnapshotBlock({
  title,
  snapshot,
  unavailableLabel,
  asOfLabel,
  passLabel,
}: {
  title: string;
  snapshot: TechnicalIndicatorSnapshot;
  unavailableLabel: string;
  asOfLabel: string;
  passLabel: string;
}) {
  const unavailable = snapshot.quality !== "ok";
  return (
    <div data-testid={`macd-kd-${snapshot.timeframe}`}>
      <h3 className="cds--productive-heading-02">{title}</h3>
      <p className="cds--helper-text-01">
        {asOfLabel}: {snapshot.asOf ?? "—"} · {passLabel}:{" "}
        {unavailable ? unavailableLabel : String(snapshot.passStatus ?? "—")}
      </p>
      {unavailable ? (
        <p className="cds--body-compact-01" data-testid={`macd-kd-${snapshot.timeframe}-unavailable`}>
          {unavailableLabel}
          {snapshot.unavailableReason ? ` (${snapshot.unavailableReason})` : ""}
        </p>
      ) : (
        <dl className="app-kv-list">
          <div>
            <dt>MACD DIF</dt>
            <dd>{formatNumber(snapshot.macdDif ?? snapshot.dif)}</dd>
          </div>
          <div>
            <dt>MACD Signal</dt>
            <dd>{formatNumber(snapshot.macdSignal ?? snapshot.signal)}</dd>
          </div>
          <div>
            <dt>MACD OSC</dt>
            <dd>{formatNumber(snapshot.macdHistogram ?? snapshot.osc)}</dd>
          </div>
          <div>
            <dt>KD K</dt>
            <dd>{formatNumber(snapshot.kdK ?? snapshot.k, 2)}</dd>
          </div>
          <div>
            <dt>KD D</dt>
            <dd>{formatNumber(snapshot.kdD ?? snapshot.d, 2)}</dd>
          </div>
        </dl>
      )}
    </div>
  );
}

export function MacdKdIndicatorsPanel({ stockId }: Props) {
  const { language, t } = useLanguage();
  const zh = language === "zh-TW";
  const query = useQuery<TechnicalIndicatorsResponse>({
    queryKey: ["technical-indicators", stockId],
    queryFn: () => fetchTechnicalIndicators(stockId),
    enabled: stockId.startsWith("TW:"),
    staleTime: 60_000,
  });

  if (!stockId.startsWith("TW:")) {
    return null;
  }

  return (
    <Tile className="app-section-tile" data-testid="macd-kd-indicators-panel">
      <p className="cds--label-01">{t("detail.macdKd.eyebrow")}</p>
      <h2 className="cds--productive-heading-03">{t("detail.macdKd.title")}</h2>
      <p className="cds--helper-text-01">{t("detail.macdKd.description")}</p>
      {query.isLoading ? <InlineLoading description={t("detail.loading")} status="active" /> : null}
      {query.isError && !query.data ? (
        <RequestStateNotice
          kind={errorToNoticeKind(query.error)}
          scopeLabel={t("detail.macdKd.title")}
        />
      ) : null}
      {query.data ? (
        <div className="app-section-stack">
          <SnapshotBlock
            title={zh ? "日線 MACD / KD" : "Daily MACD / KD"}
            snapshot={query.data.daily}
            unavailableLabel={t("detail.macdKd.unavailable")}
            asOfLabel={t("detail.macdKd.asOf")}
            passLabel={t("detail.macdKd.passStatus")}
          />
          <SnapshotBlock
            title={zh ? "月線 MACD / KD（已完成月份）" : "Monthly MACD / KD (completed months)"}
            snapshot={query.data.monthly}
            unavailableLabel={t("detail.macdKd.unavailable")}
            asOfLabel={t("detail.macdKd.asOf")}
            passLabel={t("detail.macdKd.passStatus")}
          />
          <p className="cds--helper-text-01">
            {zh
              ? `計算版本 ${query.data.calculationVersion} · 僅讀已儲存日K，不觸發研究掃描`
              : `Calculation ${query.data.calculationVersion} · stored bars only; no research scan`}
          </p>
        </div>
      ) : null}
    </Tile>
  );
}
