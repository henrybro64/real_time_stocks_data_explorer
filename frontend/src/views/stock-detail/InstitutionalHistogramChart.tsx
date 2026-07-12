"use client";

import { formatAxisDate } from "./twSignalsFormat";

type InstitutionalHistoryPoint = {
  date: string;
  value: number | null | undefined;
};

type Props = {
  dateRangeLabel: string;
  emptyLabel: string;
  language: "en" | "zh-TW";
  points: InstitutionalHistoryPoint[];
  title: string;
};

const PLOT_HEIGHT = 44;
const BASELINE_Y = 50;

export function InstitutionalHistogramChart({
  dateRangeLabel,
  emptyLabel,
  language,
  points,
  title,
}: Readonly<Props>) {
  const validPoints = points
    .map((point, index) => ({ ...point, index }))
    .filter((point) => Number.isFinite(point.value));

  if (validPoints.length === 0) {
    return (
      <section className="app-subtile app-tw-inst-chart-card" data-testid="tw-institutional-chart-empty">
        <div className="app-tw-inst-chart-header">
          <h4 className="app-tw-inst-chart-title">{title}</h4>
          {dateRangeLabel ? <p className="app-section-meta">{dateRangeLabel}</p> : null}
        </div>
        <p className="app-supporting-copy">{emptyLabel}</p>
      </section>
    );
  }

  const maxAbsValue = Math.max(...validPoints.map((point) => Math.abs(point.value as number)), 1);
  // Keep original timeline slots so missing/null values leave gaps instead of compressing bars.
  const step = 100 / Math.max(points.length, 1);
  const barWidth = Math.max(Math.min(step * 0.72, 8), 0.9);
  const startLabel = formatAxisDate(points[0]?.date ?? "", language);
  const endLabel = formatAxisDate(points[points.length - 1]?.date ?? "", language);

  return (
    <section className="app-subtile app-tw-inst-chart-card" data-testid="tw-institutional-chart">
      <div className="app-tw-inst-chart-header">
        <h4 className="app-tw-inst-chart-title">{title}</h4>
        <p className="app-section-meta">{dateRangeLabel}</p>
      </div>
      <div className="app-tw-inst-chart-shell">
        <div className="app-tw-inst-chart-y-axis" aria-hidden>
          <span>{formatAxisValue(maxAbsValue, language)}</span>
          <span>0</span>
          <span>-{formatAxisValue(maxAbsValue, language)}</span>
        </div>
        <div className="app-tw-inst-chart-plot">
          <svg
            aria-label={title}
            className="app-tw-inst-chart-svg"
            preserveAspectRatio="none"
            role="img"
            viewBox="0 0 100 100"
          >
            <line className="app-tw-inst-chart-zero-line" x1="0" x2="100" y1={BASELINE_Y} y2={BASELINE_Y} />
            {validPoints.map((point) => {
              const value = point.value as number;
              const isZero = value === 0;
              const height = isZero
                ? 0
                : Math.max((Math.abs(value) / maxAbsValue) * PLOT_HEIGHT, 1);
              const x = (point.index * step) + ((step - barWidth) / 2);
              // Keep exact zeros on the baseline with no positive/negative extent.
              const y = value > 0 ? BASELINE_Y - height : BASELINE_Y;
              return (
                <rect
                  key={`${point.date}-${point.index}`}
                  className={twMarketBarClass(value)}
                  height={height}
                  rx={0.45}
                  ry={0.45}
                  width={barWidth}
                  x={x}
                  y={y}
                >
                  <title>{`${point.date} ${formatTooltipValue(value, language)}`}</title>
                </rect>
              );
            })}
          </svg>
          <div className="app-tw-inst-chart-x-axis" aria-hidden>
            <span>{startLabel}</span>
            <span>{endLabel}</span>
          </div>
        </div>
      </div>
    </section>
  );
}

function formatAxisValue(value: number, language: "en" | "zh-TW"): string {
  return new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    maximumFractionDigits: value % 1 === 0 ? 0 : 1,
  }).format(value);
}

function twMarketBarClass(value: number): string {
  if (value > 0) {
    return "app-tw-market-bar-positive";
  }
  if (value < 0) {
    return "app-tw-market-bar-negative";
  }
  return "app-tw-market-bar-neutral";
}

function formatTooltipValue(value: number, language: "en" | "zh-TW"): string {
  const absoluteValue = new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 3,
  }).format(Math.abs(value));
  if (value > 0) {
    return `+${absoluteValue}`;
  }
  if (value < 0) {
    return `-${absoluteValue}`;
  }
  return absoluteValue;
}
