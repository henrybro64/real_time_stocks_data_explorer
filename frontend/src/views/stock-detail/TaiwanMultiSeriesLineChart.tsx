"use client";

import { useMemo, useState } from "react";

import { formatAxisDate } from "./twSignalsFormat";

export type TaiwanMultiSeriesPoint = {
  date: string;
  /** Optional display label (e.g. fiscal quarter ``2025Q3``). */
  label?: string;
  values: Record<string, number | null | undefined>;
};

export type TaiwanMultiSeriesLine = {
  key: string;
  label: string;
  color: string;
};

type Props = {
  dateRangeLabel: string;
  emptyLabel: string;
  language: "en" | "zh-TW";
  lines: TaiwanMultiSeriesLine[];
  points: TaiwanMultiSeriesPoint[];
  title: string;
  /** When set, axis/tooltip use this label instead of formatting ``date``. */
  formatPointLabel?: (point: TaiwanMultiSeriesPoint) => string;
};

const PLOT_TOP_Y = 12;
const PLOT_BOTTOM_Y = 88;
const SERIES_CLASS = [
  "app-value-chart-line",
  "app-tw-multi-series-line-2",
  "app-tw-multi-series-line-3",
  "app-tw-multi-series-line-4",
] as const;

export function TaiwanMultiSeriesLineChart({
  dateRangeLabel,
  emptyLabel,
  language,
  lines,
  points,
  title,
  formatPointLabel,
}: Readonly<Props>) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const labelFor = (point: TaiwanMultiSeriesPoint | undefined) => {
    if (!point) {
      return "";
    }
    if (formatPointLabel) {
      return formatPointLabel(point);
    }
    return formatAxisDate(point.date, language);
  };

  const plotted = useMemo(() => {
    const finiteValues = points.flatMap((point) =>
      lines
        .map((line) => point.values[line.key])
        .filter((value): value is number => typeof value === "number" && Number.isFinite(value)),
    );
    if (finiteValues.length === 0) {
      return null;
    }
    const { minValue, maxValue } = paddedValueRange(finiteValues);
    const valueSpan = maxValue - minValue || 1;
    const xDivisor = Math.max(points.length - 1, 1);
    const series = lines.map((line, lineIndex) => {
      const plottedPoints = points
        .map((point, index) => {
          const value = point.values[line.key];
          if (typeof value !== "number" || !Number.isFinite(value)) {
            return null;
          }
          return {
            date: point.date,
            index,
            value,
            x: (index / xDivisor) * 100,
            y: PLOT_BOTTOM_Y - ((value - minValue) / valueSpan) * (PLOT_BOTTOM_Y - PLOT_TOP_Y),
          };
        })
        .filter((point): point is NonNullable<typeof point> => point != null);

      const segments: string[] = [];
      let current: string[] = [];
      let previousIndex: number | null = null;
      for (const point of plottedPoints) {
        if (previousIndex != null && point.index !== previousIndex + 1) {
          if (current.length > 0) {
            segments.push(current.join(" "));
          }
          current = [];
        }
        current.push(`${point.x},${point.y}`);
        previousIndex = point.index;
      }
      if (current.length > 0) {
        segments.push(current.join(" "));
      }

      return {
        ...line,
        className: SERIES_CLASS[lineIndex % SERIES_CLASS.length],
        plottedPoints,
        segments,
      };
    });

    const zeroY =
      minValue < 0 && maxValue > 0
        ? PLOT_BOTTOM_Y - ((0 - minValue) / valueSpan) * (PLOT_BOTTOM_Y - PLOT_TOP_Y)
        : null;

    return { minValue, maxValue, series, zeroY };
  }, [lines, points]);

  if (plotted == null) {
    return (
      <section className="app-subtile app-tw-inst-chart-card" data-testid="tw-multi-series-chart-empty">
        <div className="app-tw-inst-chart-header">
          <h4 className="app-tw-inst-chart-title">{title}</h4>
          {dateRangeLabel ? <p className="app-section-meta">{dateRangeLabel}</p> : null}
        </div>
        <p className="app-supporting-copy">{emptyLabel}</p>
      </section>
    );
  }

  const startLabel = labelFor(points[0]);
  const endLabel = labelFor(points[points.length - 1]);
  const hoverPoint = hoverIndex != null ? points[hoverIndex] : null;

  return (
    <section className="app-subtile app-tw-inst-chart-card" data-testid="tw-multi-series-chart">
      <div className="app-tw-inst-chart-header">
        <h4 className="app-tw-inst-chart-title">{title}</h4>
        <p className="app-section-meta">{dateRangeLabel}</p>
      </div>
      <ul className="app-tw-multi-series-legend" aria-label={title}>
        {lines.map((line) => (
          <li key={line.key}>
            <span aria-hidden className="app-tw-multi-series-swatch" style={{ background: line.color }} />
            {line.label}
          </li>
        ))}
      </ul>
      <div className="app-tw-inst-chart-shell">
        <div className="app-tw-inst-chart-y-axis" aria-hidden>
          <span>{formatAxisValue(plotted.maxValue, language)}</span>
          <span>{formatAxisValue((plotted.minValue + plotted.maxValue) / 2, language)}</span>
          <span>{formatAxisValue(plotted.minValue, language)}</span>
        </div>
        <div className="app-tw-inst-chart-plot">
          <svg
            aria-label={title}
            className="app-tw-inst-chart-svg"
            preserveAspectRatio="none"
            role="img"
            viewBox="0 0 100 100"
            onMouseLeave={() => setHoverIndex(null)}
            onPointerLeave={() => setHoverIndex(null)}
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
            {plotted.zeroY != null ? (
              <line
                className="app-tw-inst-chart-zero-line"
                x1="0"
                x2="100"
                y1={plotted.zeroY}
                y2={plotted.zeroY}
              />
            ) : null}
            {plotted.series.map((series) =>
              series.segments.map((segment) => (
                <polyline
                  key={`${series.key}-${segment}`}
                  className={series.className}
                  fill="none"
                  points={segment}
                  stroke={series.color}
                  vectorEffect="non-scaling-stroke"
                />
              )),
            )}
            {plotted.series.flatMap((series) =>
              series.plottedPoints.map((point) => (
                <circle
                  key={`${series.key}-${point.date}-${point.index}`}
                  className="app-value-chart-dot"
                  cx={point.x}
                  cy={point.y}
                  fill={series.color}
                  r="1.2"
                  stroke={series.color}
                />
              )),
            )}
            {points.map((point, index) => {
              const xDivisor = Math.max(points.length - 1, 1);
              const x = (index / xDivisor) * 100;
              return (
                <rect
                  key={`hover-${point.date}-${index}`}
                  fill="transparent"
                  height="100"
                  width={100 / Math.max(points.length, 1)}
                  x={x - 100 / Math.max(points.length, 1) / 2}
                  y="0"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    setHoverIndex(index);
                  }}
                  onPointerEnter={() => setHoverIndex(index)}
                />
              );
            })}
          </svg>
          <div className="app-tw-inst-chart-x-axis" aria-hidden>
            <span>{startLabel}</span>
            <span>{endLabel}</span>
          </div>
          {hoverPoint ? (
            <output className="app-tw-multi-series-tooltip" data-testid="tw-multi-series-tooltip">
              <p>{labelFor(hoverPoint)}</p>
              <ul>
                {lines.map((line) => {
                  const value = hoverPoint.values[line.key];
                  return (
                    <li key={line.key}>
                      <span style={{ color: line.color }}>{line.label}</span>
                      {": "}
                      {typeof value === "number" && Number.isFinite(value)
                        ? formatAxisValue(value, language)
                        : "—"}
                    </li>
                  );
                })}
              </ul>
            </output>
          ) : null}
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
