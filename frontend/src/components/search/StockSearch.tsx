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
  /** Distinct id when multiple search tiles exist on the page (a11y). */
  searchInputId?: string;
  /** Override section headings (e.g. portfolio vs watchlist search). */
  labels?: {
    eyebrow: string;
    title: string;
    description: string;
    hint: string;
    emptyPrompt: string;
  };
  /** Pass-through to result cards (e.g. “Add to portfolio”). */
  addLabel?: string;
  removeLabel?: string;
  sectionTestId?: string;
};

export function StockSearch({
  isSearching,
  query,
  onQueryChange,
  results,
  watchlistIds,
  onToggle,
  searchInputId = "stock-search",
  labels,
  addLabel,
  removeLabel,
  sectionTestId = "stock-search-section",
}: Props) {
  const { t } = useLanguage();

  const eyebrow = labels?.eyebrow ?? "Security lookup";
  const title = labels?.title ?? t("search.title");
  const description = labels?.description ?? t("search.description");
  const hint = labels?.hint ?? t("search.hint");
  const emptyPrompt = labels?.emptyPrompt ?? t("search.emptyPrompt");

  return (
    <Tile className="app-section-tile" data-testid={sectionTestId}>
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
        </div>
        <p className="app-section-meta">{description}</p>
      </div>
      <Search
        id={searchInputId}
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
          <p className="app-section-meta">{hint}</p>
        )}
      </div>
      <div className="app-result-list">
        {query.trim().length === 0 ? (
          <Tile className="app-subtile">
            <p className="app-supporting-copy">{emptyPrompt}</p>
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
            <StockCard
              key={stock.id}
              addLabel={addLabel}
              isTracked={isTracked}
              onToggle={onToggle}
              removeLabel={removeLabel}
              stock={stock}
            />
          );
        })}
      </div>
    </Tile>
  );
}
