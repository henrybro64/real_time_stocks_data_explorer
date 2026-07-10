"use client";

type InstitutionalHistoryPoint = {
  date: string;
  value: number;
};

type Props = {
  dateRangeLabel: string;
  language: "en" | "zh-TW";
  points: InstitutionalHistoryPoint[];
  title: string;
};

const PLOT_HEIGHT = 44;
const BASELINE_Y = 50;

export function InstitutionalHistogramChart({ dateRangeLabel, language, points, title }: Props) {
  const validPoints = points.filter((point) => Number.isFinite(point.value));

  if (validPoints.length === 0) {
    return null;
  }

  const maxAbsValue = Math.max(...validPoints.map((point) => Math.abs(point.value)), 1);
  const step = 100 / validPoints.length;
  const barWidth = Math.max(Math.min(step * 0.72, 8), 0.9);
  const startLabel = formatAxisDate(validPoints[0]?.date ?? "", language);
  const endLabel = formatAxisDate(validPoints[validPoints.length - 1]?.date ?? "", language);

  return (
    <section className="app-subtile app-tw-inst-chart-card">
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
            viewBox="0 0 100 100"
          >
            <line className="app-tw-inst-chart-zero-line" x1="0" x2="100" y1={BASELINE_Y} y2={BASELINE_Y} />
            {validPoints.map((point, index) => {
              const height = Math.max((Math.abs(point.value) / maxAbsValue) * PLOT_HEIGHT, 1);
              const x = (index * step) + ((step - barWidth) / 2);
              const y = point.value >= 0 ? BASELINE_Y - height : BASELINE_Y;
              return (
                <rect
                  key={`${point.date}-${index}`}
                  className={point.value > 0 ? "app-tw-market-bar-positive" : point.value < 0 ? "app-tw-market-bar-negative" : "app-tw-market-bar-neutral"}
                  height={height}
                  rx={0.45}
                  ry={0.45}
                  width={barWidth}
                  x={x}
                  y={y}
                >
                  <title>{`${point.date} ${formatTooltipValue(point.value, language)}`}</title>
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

function formatAxisDate(rawDate: string, language: "en" | "zh-TW"): string {
  if (!rawDate) {
    return "—";
  }
  const date = new Date(`${rawDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return rawDate;
  }
  return new Intl.DateTimeFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    month: "numeric",
    day: "numeric",
  }).format(date);
}
