"use client";

import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import { useQuery } from "@tanstack/react-query";
import type { StockAnalysisResponse } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { fetchStockAnalysis } from "../../services/api/client";
import { formatCurrency, formatPercent } from "../../services/format/quote";

type Props = {
  stockId: string;
};

type DisplayCurrency = "USD" | "TWD";

function getDisplayCurrency(currency: string | null | undefined): DisplayCurrency | null {
  return currency === "USD" || currency === "TWD" ? currency : null;
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="app-stock-analysis-metric">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function AiInsightPanel({ stockId }: Props) {
  const { formatNumber } = useLanguage();
  const analysisQuery = useQuery<StockAnalysisResponse>({
    queryKey: ["stock-analysis", stockId],
    queryFn: () => fetchStockAnalysis(stockId),
    enabled: stockId.length > 0,
    retry: false,
    staleTime: 300_000,
  });

  const analysis = analysisQuery.data;
  const computed = analysis?.computedData;
  const currency = getDisplayCurrency(computed?.currency);

  const formatPrice = (value: number | null | undefined) => {
    if (value == null) {
      return "—";
    }
    if (currency) {
      return formatCurrency(value, currency);
    }
    return formatNumber(value);
  };

  const formatRatio = (value: number | null | undefined) => {
    if (value == null) {
      return "—";
    }
    return `${value.toFixed(2)}x`;
  };

  const formatPlainNumber = (value: number | null | undefined) => {
    if (value == null) {
      return "—";
    }
    return formatNumber(value);
  };

  const formatPercentValue = (value: number | null | undefined) => {
    if (value == null) {
      return "—";
    }
    return formatPercent(value);
  };

  const metrics = computed
    ? [
        ["Ticker", computed.ticker],
        ["Company", computed.companyName ?? "—"],
        ["Currency", computed.currency ?? "—"],
        ["Sector", computed.sector ?? "—"],
        ["Industry", computed.industry ?? "—"],
        ["Market cap", formatPlainNumber(computed.marketCap)],
        ["Trailing P/E", formatRatio(computed.trailingPe)],
        ["Forward P/E", formatRatio(computed.forwardPe)],
        ["Current price", formatPrice(computed.currentPrice)],
        ["1M return", formatPercentValue(computed.return1mPercent)],
        ["3M return", formatPercentValue(computed.return3mPercent)],
        ["6M return", formatPercentValue(computed.return6mPercent)],
        ["1Y return", formatPercentValue(computed.return1yPercent)],
        ["50D SMA", formatPrice(computed.sma50)],
        ["200D SMA", formatPrice(computed.sma200)],
        ["Price vs 50D SMA", formatPercentValue(computed.priceVsSma50Percent)],
        ["Price vs 200D SMA", formatPercentValue(computed.priceVsSma200Percent)],
        ["14D RSI", computed.rsi14 == null ? "—" : computed.rsi14.toFixed(2)],
        [
          "30D annualized volatility",
          computed.volatility30dAnnualized == null
            ? "—"
            : `${computed.volatility30dAnnualized.toFixed(2)}%`,
        ],
        ["1Y max drawdown", formatPercentValue(computed.maxDrawdown1yPercent)],
        ["30D average volume", formatPlainNumber(computed.averageVolume30d)],
      ]
    : [];

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">AI</p>
          <h2>AI stock analysis</h2>
        </div>
        <p className="app-section-meta">1 year of yfinance history summarized before sending data to the AI model.</p>
      </div>

      <div className="app-ai-insight-controls">
        <div className="app-stock-analysis-tags">
          {analysis?.aiProvider ? <Tag type="cool-gray">{analysis.aiProvider}</Tag> : null}
          {analysis?.aiModel ? <Tag type="outline">{analysis.aiModel}</Tag> : null}
        </div>
        <Button size="sm" kind="ghost" onClick={() => void analysisQuery.refetch()}>
          Refresh analysis
        </Button>
      </div>

      {analysisQuery.isLoading && !analysis ? (
        <InlineLoading description="Loading stock analysis..." status="active" />
      ) : null}

      {analysisQuery.error && !analysis ? (
        <p className="app-supporting-copy app-negative">
          Failed to load stock analysis. Check the backend analysis configuration and try again.
        </p>
      ) : null}

      {analysis?.warnings.length ? (
        <div className="app-stock-analysis-messages">
          {analysis.warnings.map((warning) => (
            <p key={warning} className="app-supporting-copy">
              {warning}
            </p>
          ))}
        </div>
      ) : null}

      {analysis?.errors.length ? (
        <div className="app-stock-analysis-messages">
          {analysis.errors.map((error) => (
            <p key={error} className="app-supporting-copy app-negative">
              {error}
            </p>
          ))}
        </div>
      ) : null}

      {metrics.length ? (
        <dl className="app-stock-analysis-grid">
          {metrics.map(([label, value]) => (
            <MetricCard key={label} label={label} value={value} />
          ))}
        </dl>
      ) : null}

      {analysis?.aiAnalysis ? (
        <div className="app-stock-analysis-body">
          <h3>AI explanation</h3>
          <p className="app-supporting-copy app-stock-analysis-text">{analysis.aiAnalysis}</p>
        </div>
      ) : null}

      {analysis && !analysis.aiAnalysis && !analysisQuery.isLoading ? (
        <p className="app-supporting-copy">
          AI explanation is unavailable right now. The computed metrics above still reflect the latest successful analysis payload.
        </p>
      ) : null}

      {analysis?.citations.length ? (
        <div className="app-stock-analysis-body">
          <h3>Sources</h3>
          <ul className="app-stock-analysis-links">
            {analysis.citations.map((citation) => (
              <li key={citation.url}>
                <a href={citation.url} rel="noreferrer" target="_blank">
                  {citation.title ?? citation.source ?? citation.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="app-stock-analysis-disclaimer">
        This analysis is for informational purposes only and is not financial advice.
      </p>
    </Tile>
  );
}
