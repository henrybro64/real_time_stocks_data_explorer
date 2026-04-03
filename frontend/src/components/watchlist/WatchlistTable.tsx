import { InlineLoading, Tile } from "@carbon/react";
import type { QuoteResponse } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { StockCard } from "../stock-card/StockCard";

type Props = {
  isLoading?: boolean;
  isRefreshing?: boolean;
  items: QuoteResponse[];
  onRemove: (stockId: string) => void;
};

export function WatchlistTable({
  isLoading = false,
  isRefreshing = false,
  items,
  onRemove,
}: Props) {
  const { t } = useLanguage();

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Tracked securities</p>
          <h2>{t("watchlist.title")}</h2>
        </div>
        <p className="app-section-meta">{t("watchlist.count", { count: items.length })}</p>
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
          </div>
        </>
      )}
    </Tile>
  );
}
