"use client";

import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { StockAnalysisResponse } from "@shared/api";
import { useLanguage, type AppLanguage, type TranslationKey } from "../../app/i18n";
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

const LOCALE_BY_LANGUAGE: Record<AppLanguage, string> = {
  en: "en-US",
  "zh-TW": "zh-TW",
};

function formatDateTime(value: string, language: AppLanguage): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString(LOCALE_BY_LANGUAGE[language]);
}

function formatPublishedDate(value: string, language: AppLanguage): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString(LOCALE_BY_LANGUAGE[language], {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Backend warnings/errors arrive as fixed English sentences. Map the known set
 * to localized copy and fall back to the raw text for anything unrecognized.
 */
const NOTICE_TRANSLATION_KEYS: Record<string, TranslationKey> = {
  "Failed to load 1 year of daily stock history from yfinance.":
    "stock.ai.notice.historyFetchFailed",
  "No historical price data returned from yfinance.": "stock.ai.notice.noHistory",
  "Historical price data from yfinance did not include usable adjusted or closing prices.":
    "stock.ai.notice.unusableHistory",
  "Some company metadata could not be loaded from yfinance.":
    "stock.ai.notice.metadataUnavailable",
  "Ticker appears invalid or unavailable in yfinance.": "stock.ai.notice.invalidTicker",
  "Skipping AI analysis because required yfinance historical price data is unavailable.":
    "stock.ai.notice.aiSkippedNoHistory",
  "Recent news context could not be loaded for this analysis.":
    "stock.ai.notice.newsUnavailable",
};

function translateNotice(message: string, t: (key: TranslationKey) => string): string {
  const key = NOTICE_TRANSLATION_KEYS[message];
  return key ? t(key) : message;
}

/** Strip the inline emphasis markers the model may still emit around words. */
function stripInlineMarkers(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/(^|[\s(])[*_](\S(?:.*?\S)?)[*_](?=[\s).,;:!?]|$)/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1");
}

function renderNarrativeBlocks(text: string) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Array<{ type: "h3" | "p" | "ul"; content: string | string[] }> = [];
  let paragraph: string[] = [];
  let bullets: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) {
      return;
    }
    blocks.push({ type: "p", content: paragraph.join(" ").trim() });
    paragraph = [];
  };

  const flushBullets = () => {
    if (!bullets.length) {
      return;
    }
    blocks.push({ type: "ul", content: [...bullets] });
    bullets = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushBullets();
      flushParagraph();
      continue;
    }
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      flushBullets();
      flushParagraph();
      blocks.push({ type: "h3", content: stripInlineMarkers(heading[1].trim()) });
      continue;
    }
    const bullet = /^(?:[-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      bullets.push(stripInlineMarkers(bullet[1].trim()));
      continue;
    }
    flushBullets();
    paragraph.push(stripInlineMarkers(line));
  }
  flushBullets();
  flushParagraph();

  return blocks.map((block, index) => {
    if (block.type === "h3" && typeof block.content === "string") {
      return <h3 key={`h-${index}`}>{block.content}</h3>;
    }
    if (block.type === "ul" && Array.isArray(block.content)) {
      return (
        <ul key={`ul-${index}`} className="app-stock-analysis-links">
          {block.content.map((item, itemIndex) => (
            <li key={`${index}-${itemIndex}`}>{item}</li>
          ))}
        </ul>
      );
    }
    return (
      <p key={`p-${index}`} className="app-supporting-copy app-stock-analysis-text">
        {block.content as string}
      </p>
    );
  });
}

export function AiInsightPanel({ stockId }: Props) {
  const { t, language, formatNumber } = useLanguage();
  const queryClient = useQueryClient();
  const analysisLanguage = language as AppLanguage;
  const analysisQuery = useQuery<StockAnalysisResponse>({
    queryKey: ["stock-analysis", stockId, analysisLanguage],
    queryFn: () => fetchStockAnalysis(stockId, { language: analysisLanguage }),
    enabled: stockId.length > 0,
    retry: false,
    staleTime: 300_000,
  });

  const refreshMutation = useMutation({
    mutationFn: () =>
      fetchStockAnalysis(stockId, { refresh: true, language: analysisLanguage }),
    onSuccess: (data) => {
      queryClient.setQueryData(["stock-analysis", stockId, analysisLanguage], data);
    },
  });

  const analysis = analysisQuery.data;
  const isRefreshing = refreshMutation.isPending;
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
        [t("stock.ai.metric.ticker"), computed.ticker],
        [t("stock.ai.metric.company"), computed.companyName ?? "—"],
        [t("stock.ai.metric.currency"), computed.currency ?? "—"],
        [t("stock.ai.metric.sector"), computed.sector ?? "—"],
        [t("stock.ai.metric.industry"), computed.industry ?? "—"],
        [t("stock.ai.metric.marketCap"), formatPlainNumber(computed.marketCap)],
        [t("stock.ai.metric.trailingPe"), formatRatio(computed.trailingPe)],
        [t("stock.ai.metric.forwardPe"), formatRatio(computed.forwardPe)],
        [t("stock.ai.metric.currentPrice"), formatPrice(computed.currentPrice)],
        [t("stock.ai.metric.return1m"), formatPercentValue(computed.return1mPercent)],
        [t("stock.ai.metric.return3m"), formatPercentValue(computed.return3mPercent)],
        [t("stock.ai.metric.return6m"), formatPercentValue(computed.return6mPercent)],
        [t("stock.ai.metric.return1y"), formatPercentValue(computed.return1yPercent)],
        [t("stock.ai.metric.sma50"), formatPrice(computed.sma50)],
        [t("stock.ai.metric.sma200"), formatPrice(computed.sma200)],
        [t("stock.ai.metric.priceVsSma50"), formatPercentValue(computed.priceVsSma50Percent)],
        [t("stock.ai.metric.priceVsSma200"), formatPercentValue(computed.priceVsSma200Percent)],
        [t("stock.ai.metric.rsi14"), computed.rsi14 == null ? "—" : computed.rsi14.toFixed(2)],
        [
          t("stock.ai.metric.volatility30d"),
          computed.volatility30dAnnualized == null
            ? "—"
            : `${computed.volatility30dAnnualized.toFixed(2)}%`,
        ],
        [t("stock.ai.metric.maxDrawdown1y"), formatPercentValue(computed.maxDrawdown1yPercent)],
        [t("stock.ai.metric.averageVolume30d"), formatPlainNumber(computed.averageVolume30d)],
      ]
    : [];

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{t("stock.ai.eyebrow")}</p>
          <h2>{t("stock.ai.title")}</h2>
        </div>
        <p className="app-section-meta">{t("stock.ai.description")}</p>
      </div>

      <div className="app-ai-insight-controls">
        <div className="app-stock-analysis-tags">
          {analysis?.aiProvider ? <Tag type="cool-gray">{analysis.aiProvider}</Tag> : null}
          {analysis?.aiModel ? <Tag type="outline">{analysis.aiModel}</Tag> : null}
        </div>
        <Button
          size="sm"
          kind="ghost"
          disabled={isRefreshing}
          onClick={() => {
            refreshMutation.mutate();
          }}
        >
          {t("stock.ai.refresh")}
        </Button>
      </div>

      {analysis?.analyzedAt ? (
        <p className="app-supporting-copy app-section-meta">
          {t("stock.ai.analyzedAt").replace(
            "{datetime}",
            formatDateTime(analysis.analyzedAt, analysisLanguage),
          )}
        </p>
      ) : null}

      {analysisQuery.isLoading && !analysis ? (
        <InlineLoading description={t("stock.ai.loading")} status="active" />
      ) : null}

      {isRefreshing ? (
        <InlineLoading description={t("stock.ai.refreshing")} status="active" />
      ) : null}

      {refreshMutation.isError ? (
        <p className="app-supporting-copy app-negative">
          {refreshMutation.error instanceof Error
            ? refreshMutation.error.message
            : t("stock.ai.refreshFailed")}
        </p>
      ) : null}

      {analysisQuery.error && !analysis ? (
        <p className="app-supporting-copy app-negative">{t("stock.ai.loadFailed")}</p>
      ) : null}

      {analysis?.warnings.length ? (
        <div className="app-stock-analysis-messages">
          {analysis.warnings.map((warning) => (
            <p key={warning} className="app-supporting-copy">
              {translateNotice(warning, t)}
            </p>
          ))}
        </div>
      ) : null}

      {analysis?.errors.length ? (
        <div className="app-stock-analysis-messages">
          {analysis.errors.map((error) => (
            <p key={error} className="app-supporting-copy app-negative">
              {translateNotice(error, t)}
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
          <h3>{t("stock.ai.explanation")}</h3>
          {renderNarrativeBlocks(analysis.aiAnalysis)}
        </div>
      ) : null}

      {analysis && !analysis.aiAnalysis && !analysisQuery.isLoading && !isRefreshing ? (
        <p className="app-supporting-copy">{t("stock.ai.noCache")}</p>
      ) : null}

      {analysis?.citations.length ? (
        <div className="app-stock-analysis-body">
          <h3>{t("stock.ai.sources")}</h3>
          <ul className="app-stock-analysis-links">
            {analysis.citations.map((citation) => (
              <li key={citation.url}>
                <a href={citation.url} rel="noreferrer" target="_blank">
                  {citation.title ?? citation.source ?? citation.url}
                </a>
                {citation.source || citation.publishedAt ? (
                  <span className="app-supporting-copy">
                    {" "}
                    (
                    {[
                      citation.source,
                      citation.publishedAt
                        ? formatPublishedDate(citation.publishedAt, analysisLanguage)
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    )
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="app-stock-analysis-disclaimer">{t("stock.ai.disclaimer")}</p>
    </Tile>
  );
}
