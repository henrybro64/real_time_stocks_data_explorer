import { Column, Grid, Tag, Tile } from "@carbon/react";
import type { QuoteResponse } from "@shared/api";
import { getDisplayStockName, useLanguage } from "../../app/i18n";
import { formatCurrency, formatLastUpdated, formatPercent, formatSignedNumber } from "../../services/format/quote";

type Props = {
  item: QuoteResponse;
};

export function QuoteSummary({ item }: Props) {
  const { language, t, formatNumber } = useLanguage();
  const { stock, quote } = item;

  return (
    <Tile className="app-section-tile app-quote-summary">
      <div className="app-section-header">
        <div>
          <p className="app-detail-symbol">
            {stock.symbol} <span>{stock.exchange}</span>
          </p>
          <h2>{getDisplayStockName(stock, language)}</h2>
        </div>
        <Tag type="cool-gray">{stock.currency}</Tag>
      </div>
      <div className="app-detail-price-row">
        <p className="app-detail-price">{formatCurrency(quote.lastPrice, stock.currency)}</p>
        <p className={quote.change >= 0 ? "app-positive app-detail-change" : "app-negative app-detail-change"}>
          {formatSignedNumber(quote.change, stock.currency)} ({formatPercent(quote.changePercent)})
        </p>
      </div>
      <p className="app-section-meta">{t("quote.updatedAt", { time: formatLastUpdated(quote.timestamp) })}</p>
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
