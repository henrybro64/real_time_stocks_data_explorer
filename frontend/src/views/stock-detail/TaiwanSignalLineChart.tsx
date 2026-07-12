"use client";

type TaiwanSignalLinePoint = {
  date: string;
  value: number | null | undefined;
};

type Props = {
  dateRangeLabel: string;
  emptyLabel: string;
  language: "en" | "zh-TW";
  points: TaiwanSignalLinePoint[];
  title: string;
};

const PLOT_TOP_Y = 12;
const PLOT_BOTTOM_Y = 88;

export function TaiwanSignalLineChart({
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
      <section className="app-subtile app-tw-inst-chart-card" data-testid="tw-margin-short-line-chart-empty">
        <div className="app-tw-inst-chart-header">
          <h4 className="app-tw-inst-chart-title">{title}</h4>
          {dateRangeLabel ? <p className="app-section-meta">{dateRangeLabel}</p> : null}
        </div>
        <p className="app-supporting-copy">{emptyLabel}</p>
      </section>
    );
  }

  const { minValue, maxValue } = paddedValueRange(validPoints.map((point) => point.value as number));
  const valueSpan = maxValue - minValue || 1;
  const xDivisor = Math.max(points.length - 1, 1);
  const plottedPoints = validPoints.map((point) => ({
    ...point,
    value: point.value as number,
    x: (point.index / xDivisor) * 100,
    y: PLOT_BOTTOM_Y - (((point.value as number) - minValue) / valueSpan) * (PLOT_BOTTOM_Y - PLOT_TOP_Y),
  }));
  const zeroY =
    minValue < 0 && maxValue > 0
      ? PLOT_BOTTOM_Y - ((0 - minValue) / valueSpan) * (PLOT_BOTTOM_Y - PLOT_TOP_Y)
      : null;
  const polyline = plottedPoints.map((point) => `${point.x},${point.y}`).join(" ");
  const startLabel = formatAxisDate(points[0]?.date ?? "", language);
  const endLabel = formatAxisDate(points[points.length - 1]?.date ?? "", language);

  return (
    <section className="app-subtile app-tw-inst-chart-card" data-testid="tw-margin-short-line-chart">
      <div className="app-tw-inst-chart-header">
        <h4 className="app-tw-inst-chart-title">{title}</h4>
        <p className="app-section-meta">{dateRangeLabel}</p>
      </div>
      <div className="app-tw-inst-chart-shell">
        <div className="app-tw-inst-chart-y-axis" aria-hidden>
          <span>{formatAxisValue(maxValue, language)}</span>
          <span>{formatAxisValue((minValue + maxValue) / 2, language)}</span>
          <span>{formatAxisValue(minValue, language)}</span>
        </div>
        <div className="app-tw-inst-chart-plot">
          <svg
            aria-label={title}
            className="app-tw-inst-chart-svg"
            preserveAspectRatio="none"
            role="img"
            viewBox="0 0 100 100"
          >
            {[PLOT_TOP_Y, 32, 50, 68, PLOT_BOTTOM_Y].map((position) => (
              <line
                key={`${title}-${position}`}
                className="app-chart-grid-line"
                x1="0"
                x2="100"
                y1={position}
                y2={position}
              />
            ))}
            {zeroY != null ? (
              <line className="app-tw-inst-chart-zero-line" x1="0" x2="100" y1={zeroY} y2={zeroY} />
            ) : null}
            <polyline className="app-value-chart-line" fill="none" points={polyline} vectorEffect="non-scaling-stroke" />
            {plottedPoints.map((point) => (
              <circle
                key={`${point.date}-${point.index}`}
                className="app-value-chart-dot"
                cx={point.x}
                cy={point.y}
                r="1.2"
              >
                <title>{`${point.date} ${formatAxisValue(point.value, language)}`}</title>
              </circle>
            ))}
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

function paddedValueRange(values: number[]) {
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  if (rawMin === rawMax) {
    const padding = Math.max(Math.abs(rawMax) * 0.08, 1);
    return { minValue: rawMin - padding, maxValue: rawMax + padding };
  }
  const padding = (rawMax - rawMin) * 0.08;
  return {
    minValue: rawMin - padding,
    maxValue: rawMax + padding,
  };
}

function formatAxisValue(value: number, language: "en" | "zh-TW"): string {
  return new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: Number.isInteger(value) ? 0 : 1,
  }).format(value);
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
