"use client";

import { useLanguage } from "../../app/i18n";

export type ChipEvidenceRow = {
  stockId?: string | null;
  stockName?: string | null;
  nameStatus?: string | null;
  status?: string | null;
  reason?: string | null;
  institutional?: {
    status?: string;
    reason?: string | null;
    sourceAsOf?: string | null;
    capturedAt?: string | null;
    freshnessClass?: string | null;
    stale?: boolean;
    staleCheck?: string | null;
    staleBasis?: string | null;
    fiveSessionTotalsStatus?: string;
    fiveSessionTotalsReason?: string | null;
    fiveSessionTotals?: {
      foreignNetLots?: number | null;
      trustNetLots?: number | null;
      dealerNetLots?: number | null;
      totalNetLots?: number | null;
      dates?: string[];
      note?: string | null;
      sessionCount?: number;
    } | null;
    sessions?: Array<{
      date?: string;
      foreignNetLots?: number | null;
      trustNetLots?: number | null;
      dealerNetLots?: number | null;
      totalNetLots?: number | null;
    }>;
  };
  margin?: {
    status?: string;
    reason?: string | null;
    sourceAsOf?: string | null;
    capturedAt?: string | null;
    freshnessClass?: string | null;
    stale?: boolean;
    staleCheck?: string | null;
    staleBasis?: string | null;
    balanceLots?: number | null;
    balanceDeltaLotsApprox5Sessions?: number | null;
    direction?: string;
    shortBalanceLots?: number | null;
    shortBalanceStatus?: string;
    utilization?: { status?: string; reason?: string | null };
  };
  tdcc?: {
    status?: string;
    reason?: string | null;
    sourceAsOf?: string | null;
    capturedAt?: string | null;
    freshnessClass?: string | null;
    stale?: boolean;
    staleCheck?: string | null;
    staleBasis?: string | null;
    holdersGe400LotsPercent?: number | null;
    weeklyChangePercentagePoints?: number | null;
    priorWeekDate?: string | null;
    limitation?: string;
  };
  notBuySignal?: boolean;
};

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function statusLine(
  zh: boolean,
  status: string | undefined,
  opts: {
    sourceAsOf?: string | null;
    capturedAt?: string | null;
    reason?: string | null;
    stale?: boolean;
    staleCheck?: string | null;
    freshnessUnverifiedLabel: string;
  },
): string {
  const bits: string[] = [status ?? "unavailable"];
  if (opts.staleCheck === "calendar_unavailable") {
    bits.push(opts.freshnessUnverifiedLabel);
  } else if (opts.stale || status === "stale") {
    bits.push(zh ? "已過期（顯示最後驗證結果）" : "stale (showing last verified)");
  }
  if (status === "not_fetched") {
    bits.push(zh ? "尚未抓取" : "not fetched yet");
  }
  if (opts.sourceAsOf) bits.push(`as-of ${opts.sourceAsOf}`);
  if (opts.capturedAt) bits.push(`captured ${opts.capturedAt}`);
  if (opts.reason) bits.push(opts.reason);
  return bits.join(" · ");
}

type Props = {
  row: ChipEvidenceRow;
  /** When a refetch fails but cached rows remain (#346). */
  staleCacheBanner?: { capturedAt?: string | null } | null;
};

export function ChipEvidenceCard({ row, staleCacheBanner }: Props) {
  const { language, t } = useLanguage();
  const zh = language === "zh-TW";
  const freshnessUnverifiedLabel = t("research.tw.chipFreshnessUnverifiedCalendar");
  const inst = row.institutional;
  const margin = row.margin;
  const tdcc = row.tdcc;
  const showTotals =
    (inst?.fiveSessionTotalsStatus === "ok" ||
      inst?.fiveSessionTotalsStatus === "partial") &&
    inst.fiveSessionTotals;

  if (row.status === "not_fetched" && row.reason === "stock_not_in_first_pass_snapshot") {
    return (
      <div className="app-section-stack" data-testid="chip-evidence-card">
        <p className="cds--helper-text-01" data-testid="chip-evidence-not-in-snapshot">
          {zh
            ? "此標的不在釘選的第一階段快照中。"
            : "Symbol is not in the pinned first-pass snapshot."}
        </p>
      </div>
    );
  }

  return (
    <div className="app-section-stack" data-testid="chip-evidence-card">
      {staleCacheBanner ? (
        <p className="cds--helper-text-01" data-testid="chip-evidence-stale-banner">
          {zh ? "已過期 — 最後驗證 " : "stale — last verified "}
          {staleCacheBanner.capturedAt ?? "—"}
        </p>
      ) : null}
      <p className="cds--helper-text-01">
        {zh
          ? "籌碼動向為事實證據，不是自動買進訊號。"
          : "Chip trends are factual evidence, not an automatic buy signal."}
      </p>
      <section data-testid="chip-evidence-institutional">
        <h4 className="cds--productive-heading-01">{zh ? "三大法人" : "Institutional (5d)"}</h4>
        <p className="cds--helper-text-01" data-testid="chip-evidence-institutional-status">
          {statusLine(zh, inst?.status, {
            sourceAsOf: inst?.sourceAsOf,
            capturedAt: inst?.capturedAt,
            reason: inst?.reason,
            stale: inst?.stale,
            staleCheck: inst?.staleCheck,
            freshnessUnverifiedLabel,
          })}
        </p>
        {inst?.sessions && inst.sessions.length > 0 ? (
          <ul className="cds--body-compact-01" data-testid="chip-evidence-institutional-sessions">
            {inst.sessions.map((session) => (
              <li key={session.date ?? JSON.stringify(session)}>
                {session.date ?? "—"}: F {fmt(session.foreignNetLots, 0)} / T{" "}
                {fmt(session.trustNetLots, 0)} / D {fmt(session.dealerNetLots, 0)} / Σ{" "}
                {fmt(session.totalNetLots, 0)}
              </li>
            ))}
          </ul>
        ) : null}
        {showTotals ? (
          <p className="cds--body-compact-01" data-testid="chip-evidence-institutional-totals">
            {inst?.fiveSessionTotalsStatus === "partial"
              ? zh
                ? "五日合計（部分，略過空值）"
                : "5-session totals (partial; nulls omitted)"
              : zh
                ? "五日合計（張）"
                : "5-session totals (lots)"}
            : F {fmt(inst.fiveSessionTotals?.foreignNetLots, 0)} / T{" "}
            {fmt(inst.fiveSessionTotals?.trustNetLots, 0)} / D{" "}
            {fmt(inst.fiveSessionTotals?.dealerNetLots, 0)} / Σ{" "}
            {fmt(inst.fiveSessionTotals?.totalNetLots, 0)}
          </p>
        ) : (
          <p className="cds--body-compact-01" data-testid="chip-evidence-institutional-totals">
            {zh ? "五日合計不可用" : "5-session totals unavailable"}
            {inst?.fiveSessionTotalsReason ? ` (${inst.fiveSessionTotalsReason})` : ""}
          </p>
        )}
      </section>
      <section data-testid="chip-evidence-margin">
        <h4 className="cds--productive-heading-01">{zh ? "融資" : "Margin"}</h4>
        <p className="cds--helper-text-01" data-testid="chip-evidence-margin-status">
          {statusLine(zh, margin?.status, {
            sourceAsOf: margin?.sourceAsOf,
            capturedAt: margin?.capturedAt,
            reason: margin?.reason,
            stale: margin?.stale,
            staleCheck: margin?.staleCheck,
            freshnessUnverifiedLabel,
          })}
        </p>
        <p className="cds--body-compact-01">
          {zh ? "餘額" : "Balance"}: {fmt(margin?.balanceLots, 0)} ·{" "}
          {zh ? "約五日變化" : "~5d Δ"}: {fmt(margin?.balanceDeltaLotsApprox5Sessions, 0)} ·{" "}
          {margin?.direction ?? "unavailable"}
        </p>
        <p className="cds--helper-text-01">
          {zh ? "融券" : "Short balance"}:{" "}
          {margin?.shortBalanceStatus === "ok" || margin?.shortBalanceLots != null
            ? fmt(margin?.shortBalanceLots, 0)
            : zh
              ? "不可用（未驗證）"
              : "unavailable (unverified)"}
        </p>
        <p className="cds--helper-text-01">
          {zh ? "融資使用率" : "Utilization"}: {margin?.utilization?.status ?? "unavailable"}
          {margin?.utilization?.reason ? ` (${margin.utilization.reason})` : ""}
        </p>
      </section>
      <section data-testid="chip-evidence-tdcc">
        <h4 className="cds--productive-heading-01">{zh ? "大戶動向" : "Large holders (TDCC)"}</h4>
        <p className="cds--helper-text-01" data-testid="chip-evidence-tdcc-status">
          {statusLine(zh, tdcc?.status, {
            sourceAsOf: tdcc?.sourceAsOf,
            capturedAt: tdcc?.capturedAt,
            reason: tdcc?.reason,
            stale: tdcc?.stale,
            staleCheck: tdcc?.staleCheck,
            freshnessUnverifiedLabel,
          })}
        </p>
        <p className="cds--body-compact-01">
          ≥400 {zh ? "張占比" : "lots %"}: {fmt(tdcc?.holdersGe400LotsPercent, 2)} ·{" "}
          {zh ? "週變化（百分點）" : "weekly Δ (pp)"}: {fmt(tdcc?.weeklyChangePercentagePoints, 2)}
          {tdcc?.priorWeekDate ? ` · prior ${tdcc.priorWeekDate}` : ""}
        </p>
        <p className="cds--helper-text-01">
          {tdcc?.limitation ||
            (zh
              ? "TDCC 週資料是持股快照，不是即時買賣。"
              : "TDCC weekly holdings are not a real-time trade feed.")}
        </p>
      </section>
    </div>
  );
}
