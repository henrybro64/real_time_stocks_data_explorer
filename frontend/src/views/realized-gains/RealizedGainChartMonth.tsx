import type { RealizedTradeCurrency } from "@shared/api";
import type { MonthlyRealizedGainPoint } from "./realizedGainCalculations";
import { formatSignedRealizedCurrency } from "./realizedGainFormat";

type Props = {
  point: MonthlyRealizedGainPoint;
  currency: RealizedTradeCurrency;
  maxAbsMonthlyGain: number;
};

export function RealizedGainChartMonth({ point, currency, maxAbsMonthlyGain }: Props) {
  const heightPercent =
    point.totalGain === 0 ? 0 : (Math.abs(point.totalGain) / maxAbsMonthlyGain) * 50;
  const isNegative = point.totalGain < 0;
  const title = `${point.label}: ${formatSignedRealizedCurrency(point.totalGain, currency)}`;
  const barStyle = { height: `${heightPercent}%` };

  return (
    <div className="app-realized-chart-column">
      <div className="app-realized-chart-axis">
        <div className="app-realized-chart-half app-realized-chart-half-positive">
          {!isNegative && point.totalGain !== 0 ? (
            <div
              className="app-realized-chart-bar app-realized-chart-bar-positive"
              style={barStyle}
              title={title}
            />
          ) : null}
        </div>
        <div className="app-realized-chart-zero-line" aria-hidden="true" />
        <div className="app-realized-chart-half app-realized-chart-half-negative">
          {isNegative ? (
            <div
              className="app-realized-chart-bar app-realized-chart-bar-negative"
              style={barStyle}
              title={title}
            />
          ) : null}
        </div>
      </div>
      <span className="app-realized-chart-label">{point.label.slice(5)}</span>
    </div>
  );
}
