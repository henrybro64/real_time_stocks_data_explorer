"use client";

import { Button, InlineLoading, Tile } from "@carbon/react";
import type { PortfolioHistorySeries } from "@shared/api";
import type { TimeRange } from "@shared/quote";
import { useCallback, useRef, useState } from "react";
import type { AppLanguage } from "../../app/i18n";
import { useLanguage } from "../../app/i18n";
import { formatCurrency } from "../../services/format/quote";

type Props = {
  title: string;
  description?: string;
  series: PortfolioHistorySeries | null;
  range: TimeRange;
  onZoomIn: () => void;
  onZoomOut: () => void;
  canZoomIn: boolean;
  canZoomOut: boolean;
  isLoading?: boolean;
};

function formatAxisDate(iso: string, language: AppLanguage, range: TimeRange): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const locale = language === "zh-TW" ? "zh-TW" : "en-US";
  if (range === "1D") {
    return new Intl.DateTimeFormat(locale, {
      hour: "2-digit",
      minute: "2-digit",
    }).format(d);
  }
  if (range === "1W" || range === "1M") {
    return new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "numeric",
    }).format(d);
  }
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "2-digit",
  }).format(d);
}

function formatTooltipDate(iso: string, language: AppLanguage): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

function sourceWindowLabel(sourceWindow: string, t: ReturnType<typeof useLanguage>["t"]): string {
  if (sourceWindow === "TW") {
    return t("portfolio.history.session.tw");
  }
  if (sourceWindow === "US") {
    return t("portfolio.history.session.us");
  }
  return sourceWindow;
}

export function HistoricalValueChart({
  title,
  description,
  series,
  range,
  onZoomIn,
  onZoomOut,
  canZoomIn,
  canZoomOut,
  isLoading = false,
}: Props) {
  const { t, language } = useLanguage();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [tooltipStyle, setTooltipStyle] = useState<{ left: number; top: number }>({
    left: 0,
    top: 0,
  });

  const points = series?.points ?? [];
  const chartLayout = series?.chartLayout ?? null;
  const coords = chartLayout?.points ?? [];
  const yTicks = chartLayout?.yAxisTicks ?? [];
  const xTickIndices = chartLayout?.xAxisTickIndices ?? [];
  const currency = series?.currency ?? "USD";

  const updateFromClientX = useCallback(
    (clientX: number, clientY: number) => {
      const wrap = wrapRef.current;
      if (!wrap || points.length === 0) return;
      const rect = wrap.getBoundingClientRect();
      const x = clientX - rect.left;
      const viewX = (x / rect.width) * 100;
      const raw = Math.round((viewX / 100) * (points.length - 1));
      const index = Math.min(Math.max(raw, 0), points.length - 1);
      setActiveIndex(index);

      const tooltipWidth = 220;
      const tooltipHeight = 150;
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
    [points.length],
  );

  const handlePointerMove = useCallback(
    (event: React.PointerEvent) => {
      updateFromClientX(event.clientX, event.clientY);
    },
    [updateFromClientX],
  );

  const handlePointerDown = useCallback(
    (event: React.PointerEvent) => {
      updateFromClientX(event.clientX, event.clientY);
    },
    [updateFromClientX],
  );

  const handlePointerLeave = useCallback(() => {
    setActiveIndex(null);
  }, []);

  const activePoint = activeIndex !== null ? points[activeIndex] ?? null : null;
  const activeCoord = activeIndex !== null ? coords[activeIndex] ?? null : null;

  return (
    <div className="app-chart-panel">
      <div className="app-section-header">
        <div>
          <h2 className="app-chart-heading">{title}</h2>
          {description ? <p className="app-section-meta">{description}</p> : null}
        </div>
        <div className="app-chart-toolbar">
          <span className="app-section-meta">{t("portfolio.history.range", { range })}</span>
          <div className="app-chart-toolbar-actions">
            <Button kind="ghost" size="sm" onClick={onZoomIn} disabled={!canZoomIn}>
              {t("portfolio.history.zoomIn")}
            </Button>
            <Button kind="ghost" size="sm" onClick={onZoomOut} disabled={!canZoomOut}>
              {t("portfolio.history.zoomOut")}
            </Button>
          </div>
        </div>
      </div>
      <p className="app-chart-interact-hint app-supporting-copy">{t("portfolio.history.interactHint")}</p>

      {isLoading && points.length === 0 ? (
        <InlineLoading description={t("portfolio.history.loading")} status="active" />
      ) : null}

      {!isLoading && (!series || points.length === 0 || !chartLayout || coords.length === 0 || yTicks.length === 0) ? (
        <Tile className="app-empty-state">{t("portfolio.history.empty")}</Tile>
      ) : null}

      {!isLoading && series && points.length > 0 && chartLayout && coords.length > 0 && yTicks.length > 0 ? (
        <>
          <div className="app-chart-frame">
            <div className="app-chart-y-axis" aria-hidden>
              <span className="app-chart-y-currency">{t("chart.axisYTitle", { currency })}</span>
              <div className="app-chart-y-ticks">
                {yTicks.map((value, index) => (
                  <span key={`${currency}-${index}`} className="app-chart-y-tick">
                    {formatCurrency(value, currency)}
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
                {[15, 40, 65, 90].map((position) => (
                  <line
                    key={`guide-${position}`}
                    className="app-chart-grid-line"
                    x1="0"
                    x2="100"
                    y1={position}
                    y2={position}
                  />
                ))}
                <polyline
                  className="app-value-chart-line"
                  fill="none"
                  points={chartLayout.pointsPolyline}
                  vectorEffect="non-scaling-stroke"
                />
                {coords.map((coord, index) => (
                  <circle
                    key={`${points[index]?.timestamp ?? index}-${index}`}
                    className={[
                      "app-value-chart-dot",
                      activeIndex === index ? "app-value-chart-dot-active" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    cx={coord.x}
                    cy={coord.y}
                    r={activeIndex === index ? 1.35 : 0.75}
                  />
                ))}
                {activeCoord ? (
                  <>
                    <line
                      className="app-chart-crosshair"
                      x1={activeCoord.x}
                      x2={activeCoord.x}
                      y1="0"
                      y2="100"
                      vectorEffect="non-scaling-stroke"
                    />
                    <circle
                      className="app-chart-focus-dot"
                      cx={activeCoord.x}
                      cy={activeCoord.y}
                      r="1.8"
                    />
                  </>
                ) : null}
                <rect className="app-chart-hit-layer" width="100" height="100" fill="transparent" />
              </svg>

              {activePoint ? (
                <div className="app-chart-tooltip" style={tooltipStyle}>
                  <p className="app-chart-tooltip-date">
                    {formatTooltipDate(activePoint.timestamp, language)}
                  </p>
                  <dl className="app-chart-tooltip-dl">
                    <div className="app-chart-tooltip-row">
                      <dt>{t("portfolio.history.value")}</dt>
                      <dd>{formatCurrency(activePoint.value, currency)}</dd>
                    </div>
                    <div className="app-chart-tooltip-row">
                      <dt>{t("portfolio.history.session")}</dt>
                      <dd>{sourceWindowLabel(activePoint.sourceWindow, t)}</dd>
                    </div>
                    <div className="app-chart-tooltip-row">
                      <dt>{t("portfolio.aggregate.fxRate")}</dt>
                      <dd>{`1 USD = ${activePoint.fxRate.toFixed(4)} TWD`}</dd>
                    </div>
                  </dl>
                </div>
              ) : null}
            </div>

            <div className="app-chart-corner" aria-hidden />
            <div className="app-chart-x-axis">
              <span className="app-chart-x-label">{t("chart.axisDate")}</span>
              <div className="app-chart-x-ticks">
                {xTickIndices.map((index) => {
                  const point = points[index];
                  return (
                    <span key={`${point?.timestamp ?? index}-${index}`} className="app-chart-x-tick">
                      {point ? formatAxisDate(point.timestamp, language, range) : "—"}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>

          <p className="app-section-meta">
            {t("chart.rangeSummary", {
              range,
              min: formatCurrency(chartLayout.minY, currency),
              max: formatCurrency(chartLayout.maxY, currency),
            })}
          </p>
        </>
      ) : null}
    </div>
  );
}
