import { InlineLoading, Search, Tile } from "@carbon/react";
import type { Stock } from "@shared/stock";
import { useLanguage } from "../../app/i18n";
import { StockCard } from "../stock-card/StockCard";

type Props = {
  isSearching: boolean;
  query: string;
  onQueryChange: (nextValue: string) => void;
  results: Stock[];
  watchlistIds: string[];
  onToggle: (stockId: string) => void;
};

export function StockSearch({
  isSearching,
  query,
  onQueryChange,
  results,
  watchlistIds,
  onToggle,
}: Props) {
  const { t } = useLanguage();

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Security lookup</p>
          <h2>{t("search.title")}</h2>
        </div>
        <p className="app-section-meta">{t("search.description")}</p>
      </div>
      <Search
        id="stock-search"
        labelText={t("search.label")}
        placeholder={t("search.placeholder")}
        size="lg"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
      />
      <div className="app-search-status">
        {isSearching ? (
          <InlineLoading description={t("search.loading")} status="active" />
        ) : (
          <p className="app-section-meta">{t("search.hint")}</p>
        )}
      </div>
      <div className="app-result-list">
        {query.trim().length === 0 ? (
          <Tile className="app-subtile">
            <p className="app-supporting-copy">{t("search.emptyPrompt")}</p>
          </Tile>
        ) : null}
        {query.trim().length > 0 && !isSearching && results.length === 0 ? (
          <Tile className="app-subtile">
            <p className="app-supporting-copy">{t("search.noResults")}</p>
          </Tile>
        ) : null}
        {results.map((stock) => {
          const isTracked = watchlistIds.includes(stock.id);
          return (
            <StockCard key={stock.id} isTracked={isTracked} onToggle={onToggle} stock={stock} />
          );
        })}
      </div>
    </Tile>
  );
}
