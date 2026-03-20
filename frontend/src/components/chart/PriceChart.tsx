import { Tile } from "@carbon/react";
import type { Candle } from "@shared/quote";
import { useLanguage } from "../../app/i18n";
import { formatCurrency } from "../../services/format/quote";

type Props = {
  candles: Candle[];
  currency: "USD" | "TWD";
  isActive?: boolean;
  range: string;
};

export function PriceChart({ candles, currency, isActive = true, range }: Props) {
  const { t } = useLanguage();

  if (!isActive) {
    return null;
  }

  if (candles.length === 0) {
    return <Tile className="app-empty-state">{t("chart.noData")}</Tile>;
  }

  const prices = candles.map((candle) => candle.close);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const guideLines = [15, 40, 65, 90];
  const points = candles
    .map((candle, index) => {
      const x = (index / Math.max(candles.length - 1, 1)) * 100;
      const y = max === min ? 50 : 100 - ((candle.close - min) / (max - min)) * 100;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div className="app-chart-panel">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Price history</p>
          <h2>{t("chart.title")}</h2>
        </div>
        <p className="app-section-meta">
          {t("chart.rangeSummary", {
            range,
            min: formatCurrency(min, currency),
            max: formatCurrency(max, currency),
          })}
        </p>
      </div>
      <svg className="app-chart-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
        {guideLines.map((position) => (
          <line key={position} className="app-chart-grid-line" x1="0" x2="100" y1={position} y2={position} />
        ))}
        <defs>
          <linearGradient id="chartGradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#0f62fe" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#0f62fe" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <polygon points={`0,100 ${points} 100,100`} fill="url(#chartGradient)" />
        <polyline points={points} fill="none" stroke="#0f62fe" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      </svg>
      <p className="app-section-meta">{t("chart.description")}</p>
    </div>
  );
}
