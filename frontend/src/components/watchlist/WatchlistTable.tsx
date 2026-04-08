import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import type { QuoteResponse } from "@shared/api";
import type { Stock } from "@shared/stock";
import { useLanguage } from "../../app/i18n";
import { StockCard } from "../stock-card/StockCard";

type WatchlistItem = {
  stock: Stock;
  quote?: QuoteResponse["quote"];
};

type Props = {
  isLoading?: boolean;
  isRefreshing?: boolean;
  items: WatchlistItem[];
  pendingIds?: string[];
  trackedCount?: number;
  onRemove: (stockId: string) => void;
};

export function WatchlistTable({
  isLoading = false,
  isRefreshing = false,
  items,
  pendingIds = [],
  trackedCount,
  onRemove,
}: Props) {
  const { t } = useLanguage();
  const count = trackedCount ?? items.length;

  return (
    <Tile className="app-section-tile" data-testid="watchlist-section">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Tracked securities</p>
          <h2>{t("watchlist.title")}</h2>
        </div>
        <p className="app-section-meta">{t("watchlist.count", { count })}</p>
      </div>
      {isLoading ? (
        <div className="app-watchlist-loading-wrap">
          <InlineLoading description={t("watchlist.loading")} status="active" />
        </div>
      ) : items.length === 0 ? (
        <p className="app-supporting-copy">{t("watchlist.empty")}</p>
      ) : (
        <>
          <div className="app-watchlist-refreshing-wrap">
            {isRefreshing ? (
              <InlineLoading description={t("home.summary.loading")} status="active" />
            ) : null}
          </div>
          <div className="app-watchlist-grid app-watchlist-grid-explorer">
            {items.map(({ stock, quote }) => (
              <StockCard
                key={stock.id}
                isTracked
                onToggle={onRemove}
                quote={quote}
                stock={stock}
              />
            ))}
            {pendingIds.map((stockId) => (
              <PendingWatchlistCard
                key={stockId}
                stockId={stockId}
                onRemove={onRemove}
              />
            ))}
          </div>
        </>
      )}
    </Tile>
  );
}

function PendingWatchlistCard({
  stockId,
  onRemove,
}: {
  stockId: string;
  onRemove: (stockId: string) => void;
}) {
  const { t } = useLanguage();
  const [market, symbol] = stockId.split(":", 2);

  return (
    <Tile className="app-stock-card" data-testid={`stock-card-${stockId}`}>
      <div className="app-stock-card-top">
        <div>
          <p className="app-result-symbol">
            {symbol || stockId} <span>{market || "—"}</span>
          </p>
          <p className="app-result-name">{stockId}</p>
        </div>
        <Tag type="cool-gray">{market || "—"}</Tag>
      </div>

      <div className="app-watchlist-loading-wrap">
        <InlineLoading description={t("watchlist.loading")} status="active" />
      </div>

      <div className="app-stock-card-actions">
        <Button
          data-testid={`stock-card-toggle-${stockId}`}
          kind="secondary"
          size="sm"
          onClick={() => onRemove(stockId)}
        >
          {t("stock.remove")}
        </Button>
      </div>
    </Tile>
  );
}
