"use client";

import { useLanguage } from "../../app/i18n";

export type ChipEvidenceRow = {
  stockId?: string | null;
  stockName?: string | null;
  nameStatus?: string | null;
  institutional?: {
    status?: string;
    reason?: string | null;
    sourceAsOf?: string | null;
    capturedAt?: string | null;
    freshnessClass?: string | null;
    stale?: boolean;
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

/** Match backend ``chip_evidence._sum_present`` — omit nulls; never invent zeros. */
function sumPresent(values: Array<number | null>): number | null {
  const nums = values.filter((value): value is number => value != null);
  if (!nums.length) return null;
  return nums.reduce((acc, value) => acc + value, 0);
}

function provenanceMeta(field: Record<string, unknown> | null | undefined) {
  return {
    status: String(field?.status ?? "unavailable"),
    reason: (field?.reason as string | null) ?? null,
    sourceAsOf:
      (field?.source_as_of as string | null) ??
      (field?.sourceAsOf as string | null) ??
      null,
    capturedAt:
      (field?.captured_at as string | null) ??
      (field?.capturedAt as string | null) ??
      null,
    freshnessClass:
      (field?.freshness_class as string | null) ??
      (field?.freshnessClass as string | null) ??
      null,
    stale: Boolean(field?.stale),
  };
}

/**
 * Format frozen first-pass / enrichment fields for display (#346).
 *
 * Totals semantics match ``backend/.../chip_evidence._format_institutional``:
 * - 5 complete sessions → ``ok`` full sum
 * - 5 sessions with null lots → ``partial`` totals via sum-present (omit nulls)
 * - fewer than 5 sessions → ``partial`` / ``unavailable`` with null totals
 */
export function formatChipEvidenceFromFields(
  stockId: string,
  stockName: string | null | undefined,
  nameStatus: string | null | undefined,
  fields: Record<string, unknown> | null | undefined,
): ChipEvidenceRow {
  const institutional = (fields?.institutional_5d ?? null) as Record<string, unknown> | null;
  const margin = (fields?.margin ?? null) as Record<string, unknown> | null;
  const utilization = (fields?.margin_utilization ?? null) as Record<string, unknown> | null;
  const tdcc = (fields?.tdcc_holders_ge_400_lots ?? null) as Record<string, unknown> | null;
  const instMeta = provenanceMeta(institutional);
  const marginMeta = provenanceMeta(margin);
  const tdccMeta = provenanceMeta(tdcc);
  const instValue = Array.isArray(institutional?.value) ? institutional?.value : [];
  const sessions = (instValue as Array<Record<string, unknown>>).map((row) => ({
    date: String(row.date ?? ""),
    foreignNetLots: numberOrNull(row.foreignNetLots),
    trustNetLots: numberOrNull(row.trustNetLots),
    dealerNetLots: numberOrNull(row.dealerNetLots),
    totalNetLots: numberOrNull(row.totalNetLots),
  }));
  const window = sessions.slice(-5);
  const complete =
    window.length >= 5 &&
    window.every(
      (row) =>
        row.foreignNetLots != null &&
        row.trustNetLots != null &&
        row.dealerNetLots != null &&
        row.totalNetLots != null,
    );

  let fiveSessionTotalsStatus = "unavailable";
  let fiveSessionTotalsReason: string | null = "field_absent";
  let fiveSessionTotals: {
    foreignNetLots?: number | null;
    trustNetLots?: number | null;
    dealerNetLots?: number | null;
    totalNetLots?: number | null;
    dates?: string[];
    note?: string | null;
    sessionCount?: number;
  } | null = null;

  if (window.length >= 5) {
    if (complete) {
      fiveSessionTotalsStatus = "ok";
      fiveSessionTotalsReason = null;
      fiveSessionTotals = {
        foreignNetLots: sumPresent(window.map((row) => row.foreignNetLots)),
        trustNetLots: sumPresent(window.map((row) => row.trustNetLots)),
        dealerNetLots: sumPresent(window.map((row) => row.dealerNetLots)),
        totalNetLots: sumPresent(window.map((row) => row.totalNetLots)),
        dates: window.map((row) => row.date),
        sessionCount: 5,
      };
    } else {
      fiveSessionTotalsStatus = "partial";
      fiveSessionTotalsReason = "null_lots_in_session_window";
      fiveSessionTotals = {
        foreignNetLots: sumPresent(window.map((row) => row.foreignNetLots)),
        trustNetLots: sumPresent(window.map((row) => row.trustNetLots)),
        dealerNetLots: sumPresent(window.map((row) => row.dealerNetLots)),
        totalNetLots: sumPresent(window.map((row) => row.totalNetLots)),
        dates: window.map((row) => row.date),
        sessionCount: window.length,
        note: "Partial totals omit null sessions; not invented zeros.",
      };
    }
  } else if (window.length > 0) {
    fiveSessionTotalsStatus = "partial";
    fiveSessionTotalsReason = `only_${window.length}_sessions_available`;
    fiveSessionTotals = null;
  }

  const marginValue =
    margin?.value && typeof margin.value === "object"
      ? (margin.value as Record<string, unknown>)
      : {};
  const delta = numberOrNull(marginValue.balanceDeltaLotsApprox5Sessions);
  const tdccValue =
    tdcc?.value && typeof tdcc.value === "object" ? (tdcc.value as Record<string, unknown>) : {};
  const tdccStale = Boolean(tdccValue.stale) || tdccMeta.stale;

  return {
    stockId,
    stockName,
    nameStatus,
    institutional: {
      ...instMeta,
      sessions: window,
      fiveSessionTotalsStatus,
      fiveSessionTotalsReason,
      fiveSessionTotals,
    },
    margin: {
      ...marginMeta,
      balanceLots: numberOrNull(marginValue.balanceLots),
      balanceDeltaLotsApprox5Sessions: delta,
      direction: delta == null ? "unavailable" : delta > 0 ? "increase" : delta < 0 ? "decrease" : "flat",
      shortBalanceLots: numberOrNull(
        marginValue.shortBalanceLots ??
          marginValue.borrowedShortBalanceLots ??
          marginValue.marginShortBalanceLots,
      ),
      shortBalanceStatus: numberOrNull(
        marginValue.shortBalanceLots ??
          marginValue.borrowedShortBalanceLots ??
          marginValue.marginShortBalanceLots,
      )
        ? "ok"
        : "unavailable",
      utilization: {
        status: String(utilization?.status ?? "unavailable"),
        reason: (utilization?.reason as string | null) ?? null,
      },
    },
    tdcc: {
      ...tdccMeta,
      stale: tdccStale,
      holdersGe400LotsPercent: numberOrNull(tdccValue.percent),
      weeklyChangePercentagePoints: numberOrNull(tdccValue.weeklyChangePercentPoints),
      priorWeekDate: (tdccValue.priorWeekDate as string | null) ?? null,
      limitation:
        "TDCC ≥400-lot concentration is a weekly holding snapshot, not a real-time purchase/sale feed.",
    },
    notBuySignal: true,
  };
}

function numberOrNull(value: unknown): number | null {
  if (value == null || value === "") return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function statusLine(
  zh: boolean,
  status: string | undefined,
  opts: {
    sourceAsOf?: string | null;
    capturedAt?: string | null;
    reason?: string | null;
    stale?: boolean;
  },
): string {
  const bits: string[] = [status ?? "unavailable"];
  if (opts.stale || status === "stale") {
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

export function ChipEvidenceCard({ row }: { row: ChipEvidenceRow }) {
  const { language } = useLanguage();
  const zh = language === "zh-TW";
  const inst = row.institutional;
  const margin = row.margin;
  const tdcc = row.tdcc;
  const showTotals =
    (inst?.fiveSessionTotalsStatus === "ok" ||
      inst?.fiveSessionTotalsStatus === "partial") &&
    inst.fiveSessionTotals;

  return (
    <div className="app-section-stack" data-testid="chip-evidence-card">
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
          })}
        </p>
        <p className="cds--body-compact-01">
          {zh ? "餘額" : "Balance"}: {fmt(margin?.balanceLots, 0)} ·{" "}
          {zh ? "約五日變化" : "~5d Δ"}: {fmt(margin?.balanceDeltaLotsApprox5Sessions, 0)} ·{" "}
          {margin?.direction ?? "unavailable"}
        </p>
        <p className="cds--helper-text-01">
          {zh ? "融券" : "Short balance"}:{" "}
          {margin?.shortBalanceStatus === "ok"
            ? fmt(margin.shortBalanceLots, 0)
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
