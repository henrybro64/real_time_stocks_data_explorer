"use client";

import { Tile } from "@carbon/react";
import type { Candle, ChartLayout } from "@shared/quote";
import { useCallback, useRef, useState } from "react";
import type { AppLanguage } from "../../app/i18n";
import { useLanguage } from "../../app/i18n";
import { formatCurrency } from "../../services/format/quote";

type Props = {
  candles: Candle[];
  chartLayout: ChartLayout | null;
  currency: "USD" | "TWD";
  isActive?: boolean;
  range: string;
};

type CandleGeometry = {
  x: number;
  wickTop: number;
  wickBottom: number;
  bodyTop: number;
  bodyHeight: number;
  closeY: number;
  isUp: boolean;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function priceToY(price: number, minY: number, maxY: number): number {
  if (maxY === minY) {
    return 50;
  }
  return 100 - ((price - minY) / (maxY - minY)) * 100;
}

function candleBodyWidth(points: ChartLayout["points"]): number {
  if (points.length < 2) {
    return 6;
  }

  let minStep = Number.POSITIVE_INFINITY;
  for (let index = 1; index < points.length; index += 1) {
    const step = points[index].x - points[index - 1].x;
    if (step > 0 && step < minStep) {
      minStep = step;
    }
  }

  if (!Number.isFinite(minStep)) {
    return 6;
  }

  return clamp(minStep * 0.56, 0.28, 5.4);
}

function formatAxisDate(iso: string, language: AppLanguage, range: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const locale = language === "zh-TW" ? "zh-TW" : "en-US";
  if (range === "1D") {
    return new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(d);
  }
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(d);
}

function formatTooltipDate(iso: string, language: AppLanguage, range: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const locale = language === "zh-TW" ? "zh-TW" : "en-US";
  if (range === "1D") {
    return new Intl.DateTimeFormat(locale, {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(d);
  }
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(d);
}

export function PriceChart({ candles, chartLayout, currency, isActive = true, range }: Props) {
  const { t, language, formatNumber } = useLanguage();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [tooltipStyle, setTooltipStyle] = useState<{ left: number; top: number }>({ left: 0, top: 0 });

  const pointCoords = chartLayout?.points ?? [];
  const minY = chartLayout?.minY ?? 0;
  const maxY = chartLayout?.maxY ?? 0;
  const yTicks = chartLayout?.yAxisTicks ?? [];
  const xTickIndices = chartLayout?.xAxisTickIndices ?? [];

  const updateFromClientX = useCallback(
    (clientX: number, clientY: number) => {
      const wrap = wrapRef.current;
      if (!wrap || candles.length === 0) return;
      const rect = wrap.getBoundingClientRect();
      const x = clientX - rect.left;
      const viewX = (x / rect.width) * 100;
      const raw = Math.round((viewX / 100) * (candles.length - 1));
      const index = Math.min(Math.max(raw, 0), candles.length - 1);
      setActiveIndex(index);
      const tooltipWidth = 220;
      const tooltipHeight = 200;
      let left = x + 14;
      let top = clientY - rect.top + 14;
      if (left + tooltipWidth > rect.width) {
        left = Math.max(8, x - tooltipWidth - 14);
      }
      if (top + tooltipHeight > rect.height) {
        top = Math.max(8, clientY - rect.top - tooltipHeight - 8);
      }
      setTooltipStyle({ left, top });
    },
    [candles.length],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      updateFromClientX(e.clientX, e.clientY);
    },
    [updateFromClientX],
  );

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      updateFromClientX(e.clientX, e.clientY);
    },
    [updateFromClientX],
  );

  const handlePointerLeave = useCallback(() => {
    setActiveIndex(null);
  }, []);

  if (!isActive) {
    return null;
  }

  if (candles.length === 0) {
    return <Tile className="app-empty-state">{t("chart.noData")}</Tile>;
  }

  if (!chartLayout || pointCoords.length === 0 || yTicks.length === 0) {
    return <Tile className="app-empty-state">{t("chart.noData")}</Tile>;
  }

  const bodyWidth = candleBodyWidth(pointCoords);
  const halfBodyWidth = bodyWidth / 2;
  const candleGeometries: CandleGeometry[] = candles.map((candle, index) => {
    const xSource =
      pointCoords[index]?.x ??
      (candles.length === 1 ? 50 : (index / Math.max(candles.length - 1, 1)) * 100);
    const x = clamp(xSource, halfBodyWidth, 100 - halfBodyWidth);
    const openY = priceToY(candle.open, minY, maxY);
    const closeY = priceToY(candle.close, minY, maxY);
    const highY = priceToY(candle.high, minY, maxY);
    const lowY = priceToY(candle.low, minY, maxY);
    const rawBodyHeight = Math.abs(openY - closeY);
    const normalizedBodyHeight = Math.max(rawBodyHeight, 0.9);
    const bodyCenterY = (openY + closeY) / 2;

    return {
      x,
      wickTop: Math.min(highY, lowY),
      wickBottom: Math.max(highY, lowY),
      bodyTop: rawBodyHeight < 0.9 ? bodyCenterY - normalizedBodyHeight / 2 : Math.min(openY, closeY),
      bodyHeight: normalizedBodyHeight,
      closeY,
      isUp: candle.close >= candle.open,
    };
  });

  const activeCandle = activeIndex !== null ? candles[activeIndex] ?? null : null;
  const activeGeometry = activeIndex !== null ? candleGeometries[activeIndex] ?? null : null;
  const activeCoord = activeGeometry !== null ? { x: activeGeometry.x, y: activeGeometry.closeY } : null;

  const guideLines = [15, 40, 65, 90];

  return (
    <div className="app-chart-panel">
      <div className="app-section-header">
        <div>
          <h2 className="app-chart-heading">{t("chart.title")}</h2>
        </div>
        <p className="app-section-meta">
          {t("chart.rangeSummary", {
            range,
            min: formatCurrency(minY, currency),
            max: formatCurrency(maxY, currency),
          })}
        </p>
      </div>
      <p className="app-chart-interact-hint app-supporting-copy">{t("chart.interactHint")}</p>

      <div className="app-chart-frame">
        <div className="app-chart-y-axis" aria-hidden>
          <span className="app-chart-y-currency">{t("chart.axisYTitle", { currency })}</span>
          <div className="app-chart-y-ticks">
            {yTicks.map((price, i) => (
              <span key={`y-${i}`} className="app-chart-y-tick">
                {formatCurrency(price, currency)}
              </span>
            ))}
          </div>
        </div>

        <div
          ref={wrapRef}
          className="app-chart-svg-wrap"
          onPointerMove={handlePointerMove}
          onPointerDown={handlePointerDown}
          onPointerLeave={handlePointerLeave}
          role="presentation"
        >
          <svg className="app-chart-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
            {guideLines.map((position) => (
              <line key={position} className="app-chart-grid-line" x1="0" x2="100" y1={position} y2={position} />
            ))}
            {candleGeometries.map((geometry, index) => (
              <g key={`${candles[index]?.time ?? index}-${index}`}>
                <line
                  className={[
                    "app-chart-candle-wick",
                    geometry.isUp ? "app-chart-candle-wick-up" : "app-chart-candle-wick-down",
                    activeIndex === index ? "app-chart-candle-wick-active" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  x1={geometry.x}
                  x2={geometry.x}
                  y1={geometry.wickTop}
                  y2={geometry.wickBottom}
                  vectorEffect="non-scaling-stroke"
                />
                <rect
                  className={[
                    "app-chart-candle-body",
                    geometry.isUp ? "app-chart-candle-body-up" : "app-chart-candle-body-down",
                    activeIndex === index ? "app-chart-candle-body-active" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  x={geometry.x - halfBodyWidth}
                  y={geometry.bodyTop}
                  width={bodyWidth}
                  height={geometry.bodyHeight}
                  rx={Math.min(bodyWidth * 0.12, 0.24)}
                />
              </g>
            ))}

            {activeCoord !== null ? (
              <>
                <line
                  className="app-chart-crosshair app-chart-crosshair-v"
                  x1={activeCoord.x}
                  x2={activeCoord.x}
                  y1="0"
                  y2="100"
                  vectorEffect="non-scaling-stroke"
                />
                <line
                  className="app-chart-crosshair app-chart-crosshair-h"
                  x1="0"
                  x2="100"
                  y1={activeCoord.y}
                  y2={activeCoord.y}
                  vectorEffect="non-scaling-stroke"
                />
                <circle
                  className="app-chart-focus-dot"
                  cx={activeCoord.x}
                  cy={activeCoord.y}
                  r="1.8"
                  vectorEffect="non-scaling-stroke"
                />
              </>
            ) : null}

            <rect width="100" height="100" fill="transparent" className="app-chart-hit-layer" pointerEvents="all" />
          </svg>

          {activeCandle !== null ? (
            <div className="app-chart-tooltip" style={{ left: tooltipStyle.left, top: tooltipStyle.top }}>
              <p className="app-chart-tooltip-date">{formatTooltipDate(activeCandle.time, language, range)}</p>
              <dl className="app-chart-tooltip-dl">
                <div className="app-chart-tooltip-row">
                  <dt>{t("quote.open")}</dt>
                  <dd>{formatCurrency(activeCandle.open, currency)}</dd>
                </div>
                <div className="app-chart-tooltip-row">
                  <dt>{t("quote.high")}</dt>
                  <dd>{formatCurrency(activeCandle.high, currency)}</dd>
                </div>
                <div className="app-chart-tooltip-row">
                  <dt>{t("quote.low")}</dt>
                  <dd>{formatCurrency(activeCandle.low, currency)}</dd>
                </div>
                <div className="app-chart-tooltip-row">
                  <dt>{t("chart.candleClose")}</dt>
                  <dd>{formatCurrency(activeCandle.close, currency)}</dd>
                </div>
                <div className="app-chart-tooltip-row">
                  <dt>{t("stock.volume")}</dt>
                  <dd>{formatNumber(activeCandle.volume)}</dd>
                </div>
              </dl>
            </div>
          ) : null}
        </div>

        <div className="app-chart-corner" aria-hidden />
        <div className="app-chart-x-axis">
          <span className="app-chart-x-label">{t("chart.axisDate")}</span>
          <div className="app-chart-x-ticks">
            {xTickIndices
              .filter((idx) => candles[idx] != null)
              .map((idx) => (
                <span key={idx} className="app-chart-x-tick">
                  {formatAxisDate(candles[idx].time, language, range)}
                </span>
              ))}
          </div>
        </div>
      </div>

      <p className="app-section-meta">{t("chart.description")}</p>
    </div>
  );
}
