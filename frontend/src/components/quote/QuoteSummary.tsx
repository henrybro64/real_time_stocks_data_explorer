import { Button, Column, Grid, Tag, Tile } from "@carbon/react";
import type { QuoteResponse } from "@shared/api";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { QuoteFreshnessTag } from "../state/QuoteFreshnessTag";
import { formatCurrency, formatLastUpdated, formatPercent, formatSignedNumber } from "../../services/format/quote";
import { priceToneClassForMarket } from "../../services/market-data/priceTone";

type Props = {
  item: QuoteResponse;
  refreshFailed?: boolean;
};

export function QuoteSummary({ item, refreshFailed = false }: Props) {
  const { language, t, formatNumber } = useLanguage();
  const { stock, quote } = item;

  return (
    <Tile className="app-section-tile app-quote-summary" data-testid="quote-summary-section">
      <div className="app-section-header">
        <div>
          <p className="app-detail-symbol">
            {stock.symbol} <span>{stock.exchange}</span>
          </p>
          <h2>{getDisplayStockName(stock, language)}</h2>
        </div>
        <div className="app-alerts-summary-actions">
          <Tag type="cool-gray">{stock.currency}</Tag>
          <Button
            data-testid="quote-create-alert-button"
            href={`/alerts?stockId=${encodeURIComponent(stock.id)}`}
            kind="ghost"
            size="sm"
          >
            {t("detail.createAlert")}
          </Button>
        </div>
      </div>
      <div className="app-detail-price-row">
        <p className="app-detail-price">{formatCurrency(quote.lastPrice, stock.currency)}</p>
        <p
          className={`${priceToneClassForMarket(stock.market, quote.change)} app-detail-change`}
          data-testid="quote-summary-change"
        >
          {formatSignedNumber(quote.change, stock.currency)} ({formatPercent(quote.changePercent)})
        </p>
      </div>
      <div className="app-quote-status-row">
        <p className="app-section-meta">{t("quote.updatedAt", { time: formatLastUpdated(quote.timestamp) })}</p>
        <QuoteFreshnessTag
          isMarketOpen={quote.isMarketOpen}
          refreshFailed={refreshFailed}
          timestamp={quote.timestamp}
        />
      </div>
      <Grid className="app-stat-grid" condensed fullWidth>
        <Column className="app-stat-column" sm={2} md={4} lg={3}>
          <Stat label={t("stock.prevClose")} value={formatCurrency(quote.previousClose, stock.currency)} />
        </Column>
        <Column className="app-stat-column" sm={2} md={4} lg={3}>
          <Stat label={t("quote.open")} value={quote.open ? formatCurrency(quote.open, stock.currency) : "-"} />
        </Column>
        <Column className="app-stat-column" sm={2} md={4} lg={3}>
          <Stat label={t("quote.high")} value={quote.high ? formatCurrency(quote.high, stock.currency) : "-"} />
        </Column>
        <Column className="app-stat-column" sm={2} md={4} lg={3}>
          <Stat label={t("quote.low")} value={quote.low ? formatCurrency(quote.low, stock.currency) : "-"} />
        </Column>
        <Column className="app-stat-column" sm={4} md={8} lg={4}>
          <Stat label={t("stock.volume")} value={quote.volume ? formatNumber(quote.volume) : "-"} />
        </Column>
      </Grid>
    </Tile>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Tile className="app-subtile app-stat-card">
      <p className="app-section-meta">{label}</p>
      <strong>{value}</strong>
    </Tile>
  );
}
