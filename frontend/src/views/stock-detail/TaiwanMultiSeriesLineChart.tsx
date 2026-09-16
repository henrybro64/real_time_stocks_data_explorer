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
  /** Independent Y scale; defaults to left (percentage) axis. */
  axis?: "left" | "right";
  /** Optional unit suffix for tooltip values (e.g. `%`, `TWD`). */
  unit?: string;
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
  /** Localized label for missing tooltip values. */
  missingValueLabel?: string;
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
  missingValueLabel = "—",
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
    const leftLines = lines.filter((line) => (line.axis ?? "left") === "left");
    const rightLines = lines.filter((line) => line.axis === "right");

    const valuesFor = (axisLines: TaiwanMultiSeriesLine[]) =>
      points.flatMap((point) =>
        axisLines
          .map((line) => point.values[line.key])
          .filter((value): value is number => typeof value === "number" && Number.isFinite(value)),
      );

    const leftValues = valuesFor(leftLines);
    const rightValues = valuesFor(rightLines);
    if (leftValues.length === 0 && rightValues.length === 0) {
      return null;
    }

    const leftRange = leftValues.length > 0 ? paddedValueRange(leftValues) : null;
    const rightRange = rightValues.length > 0 ? paddedValueRange(rightValues) : null;
    const xDivisor = Math.max(points.length - 1, 1);

    const yFor = (
      value: number,
      range: { minValue: number; maxValue: number } | null,
    ): number | null => {
      if (range == null) {
        return null;
      }
      const span = range.maxValue - range.minValue || 1;
      return PLOT_BOTTOM_Y - ((value - range.minValue) / span) * (PLOT_BOTTOM_Y - PLOT_TOP_Y);
    };

    const series = lines.map((line, lineIndex) => {
      const axis = line.axis ?? "left";
      const range = axis === "right" ? rightRange : leftRange;
      const plottedPoints = points
        .map((point, index) => {
          const value = point.values[line.key];
          if (typeof value !== "number" || !Number.isFinite(value)) {
            return null;
          }
          const y = yFor(value, range);
          if (y == null) {
            return null;
          }
          return {
            date: point.date,
            index,
            value,
            x: (index / xDivisor) * 100,
            y,
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
        axis,
        className: SERIES_CLASS[lineIndex % SERIES_CLASS.length],
        plottedPoints,
        segments,
      };
    });

    const leftZeroY =
      leftRange != null && leftRange.minValue < 0 && leftRange.maxValue > 0
        ? yFor(0, leftRange)
        : null;

    return {
      leftRange,
      rightRange,
      series,
      zeroY: leftZeroY,
      hasRightAxis: rightRange != null,
    };
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
  const shellClass = plotted.hasRightAxis
    ? "app-tw-inst-chart-shell app-tw-inst-chart-shell--dual"
    : "app-tw-inst-chart-shell";

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
      <div className={shellClass}>
        <div className="app-tw-inst-chart-y-axis" aria-hidden data-testid="tw-multi-series-left-axis">
          {plotted.leftRange ? (
            <>
              <span>{formatAxisValue(plotted.leftRange.maxValue, language)}</span>
              <span>
                {formatAxisValue(
                  (plotted.leftRange.minValue + plotted.leftRange.maxValue) / 2,
                  language,
                )}
              </span>
              <span>{formatAxisValue(plotted.leftRange.minValue, language)}</span>
            </>
          ) : (
            <>
              <span />
              <span />
              <span />
            </>
          )}
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
                  data-axis={series.axis}
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
                  data-axis={series.axis}
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
                  const formatted =
                    typeof value === "number" && Number.isFinite(value)
                      ? formatTooltipValue(value, language, line.unit)
                      : missingValueLabel;
                  return (
                    <li key={line.key}>
                      <span style={{ color: line.color }}>{line.label}</span>
                      {": "}
                      {formatted}
                    </li>
                  );
                })}
              </ul>
            </output>
          ) : null}
        </div>
        {plotted.hasRightAxis && plotted.rightRange ? (
          <div
            className="app-tw-inst-chart-y-axis app-tw-inst-chart-y-axis--right"
            aria-hidden
            data-testid="tw-multi-series-right-axis"
          >
            <span>{formatAxisValue(plotted.rightRange.maxValue, language)}</span>
            <span>
              {formatAxisValue(
                (plotted.rightRange.minValue + plotted.rightRange.maxValue) / 2,
                language,
              )}
            </span>
            <span>{formatAxisValue(plotted.rightRange.minValue, language)}</span>
          </div>
        ) : null}
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

function formatTooltipValue(
  value: number,
  language: "en" | "zh-TW",
  unit: string | undefined,
): string {
  // Price series (e.g. TWD weekly close) keep two decimal places; percentage
  // tooltips stay compact like the axis labels.
  const isPriceUnit = unit != null && unit !== "%" && !unit.includes("%");
  const formatted = new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    minimumFractionDigits: isPriceUnit ? 2 : 0,
    maximumFractionDigits: isPriceUnit ? 2 : Number.isInteger(value) ? 0 : 1,
  }).format(value);
  return unit ? `${formatted} ${unit}` : formatted;
}
