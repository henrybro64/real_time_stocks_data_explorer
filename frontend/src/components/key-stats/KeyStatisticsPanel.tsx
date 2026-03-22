"use client";

import type { ReactNode } from "react";
import { InlineLoading, Tile } from "@carbon/react";
import { Information } from "@carbon/icons-react";
import type { KeyStatistics } from "@shared/api";
import type { CurrencyCode } from "@shared/stock";
import { useLanguage } from "../../app/i18n";
import { formatCurrency } from "../../services/format/quote";

type Props = {
  stats: KeyStatistics | null;
  currency: CurrencyCode;
  isLoading?: boolean;
};

function InfoHint() {
  const { t } = useLanguage();
  return (
    <span className="app-key-stats-info" title={t("keyStats.infoHint")}>
      <Information size={14} aria-hidden />
    </span>
  );
}

function StatRow({ label, value, hint }: { label: string; value: ReactNode; hint?: boolean }) {
  return (
    <div className="app-key-stats-row">
      <span className="app-key-stats-label">
        {label}
        {hint ? <InfoHint /> : null}
      </span>
      <span className="app-key-stats-value">{value}</span>
    </div>
  );
}

function RangeBar({
  low,
  high,
  currency,
  lowExtra,
  highExtra,
  markerPercent,
}: {
  low: number | null;
  high: number | null;
  currency: CurrencyCode;
  lowExtra?: string;
  highExtra?: string;
  /** 0–100 from backend; no client-side price math. */
  markerPercent: number | null;
}) {
  const { t } = useLanguage();
  if (low == null || high == null || high <= low || markerPercent == null) {
    return <span className="app-key-stats-muted">{t("keyStats.na")}</span>;
  }
  const pct = markerPercent;
  return (
    <div className="app-key-stats-range">
      <div className="app-key-stats-range-track" aria-hidden>
        <div className="app-key-stats-range-marker" style={{ left: `${pct}%` }} />
      </div>
      <div className="app-key-stats-range-labels">
        <span>
          {t("keyStats.low")}: {formatCurrency(low, currency)}
          {lowExtra ? ` (${lowExtra})` : ""}
        </span>
        <span>
          {t("keyStats.high")}: {formatCurrency(high, currency)}
          {highExtra ? ` (${highExtra})` : ""}
        </span>
      </div>
    </div>
  );
}

function isFundamentalsMissing(s: KeyStatistics): boolean {
  return (
    s.marketCap == null &&
    s.sharesOutstanding == null &&
    s.beta == null &&
    s.peTtm == null &&
    s.epsTtm == null &&
    s.dividendQuarterly == null &&
    s.dividendYieldPercent == null &&
    s.exDividendDate == null &&
    s.dividendPayDate == null &&
    s.nextEarningsDate == null
  );
}

function fmtDate(iso: string | null | undefined, language: string): string {
  if (!iso) return "";
  const d = new Date(iso.includes("T") ? iso : `${iso}T12:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const locale = language === "zh-TW" ? "zh-TW" : "en-US";
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

export function KeyStatisticsPanel({ stats, currency, isLoading = false }: Props) {
  const { t, language, formatNumber } = useLanguage();

  if (isLoading) {
    return (
      <Tile className="app-key-stats-tile">
        <p className="app-eyebrow">{t("keyStats.title")}</p>
        <InlineLoading description={t("detail.loading")} status="active" />
      </Tile>
    );
  }

  if (!stats) {
    return null;
  }

  const na = t("keyStats.na");
  const fmt = (n: number | null | undefined) =>
    n == null || Number.isNaN(n) ? na : formatCurrency(n, currency);
  const fmtVol = (n: number | null | undefined) => (n == null ? na : formatNumber(n));

  const divYield =
    stats.dividendQuarterly != null && stats.dividendYieldPercent != null
      ? `${formatCurrency(stats.dividendQuarterly, currency)} / ${stats.dividendYieldPercent.toFixed(2)}%`
      : stats.dividendQuarterly != null
        ? formatCurrency(stats.dividendQuarterly, currency)
        : stats.dividendYieldPercent != null
          ? `${stats.dividendYieldPercent.toFixed(2)}%`
          : na;

  const showFundamentalsNote = isFundamentalsMissing(stats);

  return (
    <Tile className="app-key-stats-tile">
      <h3 className="app-key-stats-title">{t("keyStats.title")}</h3>
      {showFundamentalsNote ? (
        <p className="app-key-stats-fundamentals-note app-supporting-copy">{t("keyStats.fundamentalsUnavailable")}</p>
      ) : null}
      <div className="app-key-stats-grid">
        <div className="app-key-stats-col">
          <StatRow label={t("keyStats.open")} value={fmt(stats.open)} />
          <StatRow label={t("keyStats.previousClose")} value={fmt(stats.previousClose)} />
          <StatRow label={t("keyStats.volume")} value={fmtVol(stats.volume)} />
          <StatRow label={t("keyStats.avgVolume10d")} value={fmtVol(stats.avgVolume10d)} />
          <StatRow label={t("keyStats.marketCap")} value={stats.marketCap ?? na} hint />
          <div className="app-key-stats-row app-key-stats-row-block">
            <span className="app-key-stats-label">{t("keyStats.dayRange")}</span>
            <div className="app-key-stats-value app-key-stats-value-wide">
              <RangeBar
                currency={currency}
                high={stats.dayHigh}
                low={stats.dayLow}
                markerPercent={stats.dayRangeMarkerPercent ?? null}
              />
            </div>
          </div>
          <div className="app-key-stats-row app-key-stats-row-block">
            <span className="app-key-stats-label">{t("keyStats.week52Range")}</span>
            <div className="app-key-stats-value app-key-stats-value-wide">
              <RangeBar
                currency={currency}
                high={stats.fiftyTwoWeekHigh}
                low={stats.fiftyTwoWeekLow}
                highExtra={stats.fiftyTwoWeekHighDate ? fmtDate(stats.fiftyTwoWeekHighDate, language) : undefined}
                lowExtra={stats.fiftyTwoWeekLowDate ? fmtDate(stats.fiftyTwoWeekLowDate, language) : undefined}
                markerPercent={stats.week52RangeMarkerPercent ?? null}
              />
            </div>
          </div>
        </div>

        <div className="app-key-stats-col">
          <StatRow label={t("keyStats.sharesOutstanding")} value={stats.sharesOutstanding ?? na} hint />
          <StatRow label={t("keyStats.beta")} value={stats.beta != null ? String(stats.beta) : na} hint />
          <StatRow label={t("keyStats.peTtm")} value={stats.peTtm != null ? `${stats.peTtm.toFixed(2)}x` : na} hint />
          <StatRow label={t("keyStats.epsTtm")} value={stats.epsTtm != null ? formatCurrency(stats.epsTtm, currency) : na} hint />
          <StatRow label={t("keyStats.dividendYield")} value={divYield} />
          <StatRow label={t("keyStats.exDividendDate")} value={stats.exDividendDate ? fmtDate(stats.exDividendDate, language) : na} />
          <StatRow label={t("keyStats.dividendPayDate")} value={stats.dividendPayDate ? fmtDate(stats.dividendPayDate, language) : na} />
          <StatRow label={t("keyStats.nextEarnings")} value={stats.nextEarningsDate ? fmtDate(stats.nextEarningsDate, language) : na} />
        </div>
      </div>
    </Tile>
  );
}
