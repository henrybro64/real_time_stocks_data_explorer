import { Button, InlineLoading, Tag, Tile } from "@carbon/react";
import type { QuoteResponse } from "@shared/api";
import type { QuoteFreshnessMeta } from "@shared/quote";
import type { Stock } from "@shared/stock";
import { useMemo, useState } from "react";
import { useLanguage } from "../../app/i18n";
import {
  classifyMarket,
  formatListingSymbol,
  partitionByMarket,
  type ClassifiedMarket,
} from "../../services/market-data/classifyMarket";
import type { RequestNoticeKind } from "../state/RequestStateNotice";
import { RequestStateNotice } from "../state/RequestStateNotice";
import { StockCard } from "../stock-card/StockCard";

type WatchlistItem = {
  stock: Stock;
  quote?: QuoteResponse["quote"];
  freshness?: QuoteFreshnessMeta | null;
};

type Props = {
  isLoading?: boolean;
  isRefreshing?: boolean;
  items: WatchlistItem[];
  noticeKind?: RequestNoticeKind | null;
  pendingIds?: string[];
  trackedCount?: number;
  onRemove: (stockId: string) => void;
  onRetryQuote?: () => void;
  isRetryingQuote?: boolean;
  warmingTimedOutById?: Record<string, boolean>;
};

export function WatchlistTable({
  isLoading = false,
  isRefreshing = false,
  items,
  noticeKind = null,
  pendingIds = [],
  trackedCount,
  onRemove,
  onRetryQuote,
  isRetryingQuote = false,
  warmingTimedOutById = {},
}: Props) {
  const { t } = useLanguage();
  const count = trackedCount ?? items.length;
  const showOnlyNotice = noticeKind !== null && items.length === 0 && pendingIds.length === 0;
  const sections = useMemo(() => groupWatchlistEntries(items, pendingIds), [items, pendingIds]);

  return (
    <Tile className="app-section-tile" data-testid="watchlist-section">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Tracked securities</p>
          <h2>{t("watchlist.title")}</h2>
        </div>
        <div className="app-watchlist-header-actions">
          <p className="app-section-meta">{t("watchlist.count", { count })}</p>
          <Button href="/alerts" kind="ghost" size="sm">
            {t("watchlist.manageAlerts")}
          </Button>
        </div>
      </div>
      {noticeKind ? (
        <div className="app-state-notice-wrap">
          <RequestStateNotice kind={noticeKind} scopeLabel={t("watchlist.title")} />
        </div>
      ) : null}
      {isLoading ? (
        <div className="app-watchlist-loading-wrap">
          <InlineLoading description={t("watchlist.loading")} status="active" />
        </div>
      ) : items.length === 0 && pendingIds.length === 0 && !showOnlyNotice ? (
        <p className="app-supporting-copy">{t("watchlist.empty")}</p>
      ) : !showOnlyNotice ? (
        <>
          <div className="app-watchlist-refreshing-wrap">
            {isRefreshing ? (
              <InlineLoading description={t("home.summary.loading")} status="active" />
            ) : null}
          </div>
          <div className="app-watchlist-markets">
            {sections.map((section) => (
              <WatchlistMarketSection
                key={section.market}
                countLabel={t(
                  section.entries.length === 1
                    ? "watchlist.sectionCount.one"
                    : "watchlist.sectionCount",
                  { count: section.entries.length },
                )}
                entries={section.entries}
                isRetryingQuote={isRetryingQuote}
                market={section.market}
                title={t(section.titleKey)}
                warmingTimedOutById={warmingTimedOutById}
                onRemove={onRemove}
                onRetryQuote={onRetryQuote}
              />
            ))}
          </div>
        </>
      ) : null}
    </Tile>
  );
}

const MARKET_SECTIONS: Array<{
  market: ClassifiedMarket;
  titleKey: "watchlist.market.tw" | "watchlist.market.us" | "watchlist.market.unknown";
  flag: string | null;
}> = [
  { market: "TW", titleKey: "watchlist.market.tw", flag: "🇹🇼" },
  { market: "US", titleKey: "watchlist.market.us", flag: "🇺🇸" },
  { market: "unknown", titleKey: "watchlist.market.unknown", flag: null },
];

type WatchlistEntry =
  | { kind: "item"; item: WatchlistItem }
  | { kind: "pending"; stockId: string };

function groupWatchlistEntries(items: WatchlistItem[], pendingIds: string[]) {
  const entries: WatchlistEntry[] = [
    ...items.map((item) => ({ kind: "item" as const, item })),
    ...pendingIds.map((stockId) => ({ kind: "pending" as const, stockId })),
  ];
  const grouped = partitionByMarket(entries, (entry) =>
    entry.kind === "item"
      ? classifyMarket(entry.item.stock)
      : classifyMarket({ id: entry.stockId }),
  );
  return MARKET_SECTIONS.flatMap((section) => {
    const sectionEntries = grouped[section.market];
    return sectionEntries.length > 0 ? [{ ...section, entries: sectionEntries }] : [];
  });
}

function WatchlistMarketSection({
  market,
  title,
  countLabel,
  entries,
  isRetryingQuote,
  warmingTimedOutById,
  onRemove,
  onRetryQuote,
}: {
  market: ClassifiedMarket;
  title: string;
  countLabel: string;
  entries: WatchlistEntry[];
  isRetryingQuote: boolean;
  warmingTimedOutById: Record<string, boolean>;
  onRemove: (stockId: string) => void;
  onRetryQuote?: () => void;
}) {
  const flag = MARKET_SECTIONS.find((section) => section.market === market)?.flag;
  const [open, setOpen] = useState(true);

  return (
    <details
      className="app-watchlist-market"
      data-testid={`watchlist-market-${market}`}
      open={open}
      onToggle={(event) => {
        const nextOpen = event.currentTarget.open;
        setOpen((current) => (current === nextOpen ? current : nextOpen));
      }}
    >
      <summary>
        <span className="app-watchlist-market-heading">
          <h3 className="app-watchlist-market-title">
            {flag ? <span aria-hidden="true">{flag}</span> : null}
            {title}
          </h3>
        </span>
        <span className="app-section-meta">{countLabel}</span>
      </summary>
      <hr className="app-watchlist-market-rule" />
      <div className="app-watchlist-grid app-watchlist-grid-explorer">
        {entries.map((entry) =>
          entry.kind === "item" ? (
            <StockCard
              key={entry.item.stock.id}
              freshness={entry.item.freshness}
              isRetryingQuote={isRetryingQuote}
              isTracked
              onRetryQuote={onRetryQuote}
              onToggle={onRemove}
              quote={entry.item.quote}
              stock={entry.item.stock}
              warmingTimedOut={Boolean(warmingTimedOutById[entry.item.stock.id])}
            />
          ) : (
            <PendingWatchlistCard key={entry.stockId} stockId={entry.stockId} onRemove={onRemove} />
          ),
        )}
      </div>
    </details>
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
  const listingSymbol = formatListingSymbol({ id: stockId, market, symbol });

  return (
    <Tile className="app-stock-card" data-testid={`stock-card-${stockId}`}>
      <div className="app-stock-card-top">
        <div>
          <p className="app-result-symbol">
            {listingSymbol} <span>{market || "—"}</span>
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
