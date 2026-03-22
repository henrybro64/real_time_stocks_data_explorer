"use client";

import { Tile } from "@carbon/react";
import type { Candle, ChartLayout } from "@shared/quote";
import { useCallback, useId, useRef, useState } from "react";
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

  const chartGradientId = useId().replace(/:/g, "");

  const pointCoords = chartLayout?.points ?? [];
  const points = chartLayout?.pointsPolyline ?? "";
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

  if (!chartLayout || !points) {
    return <Tile className="app-empty-state">{t("chart.noData")}</Tile>;
  }

  const activeCandle = activeIndex !== null ? candles[activeIndex] : null;
  const activeCoord = activeIndex !== null ? pointCoords[activeIndex] : null;

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
            <defs>
              <linearGradient id={chartGradientId} x1="0%" y1="0%" x2="0%" y2="100%">
                <stop offset="0%" stopColor="#0f62fe" stopOpacity="0.28" />
                <stop offset="100%" stopColor="#0f62fe" stopOpacity="0.02" />
              </linearGradient>
            </defs>
            <polygon points={`0,100 ${points} 100,100`} fill={`url(#${chartGradientId})`} />
            <polyline points={points} fill="none" stroke="#0f62fe" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />

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
            {xTickIndices.map((idx) => (
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
