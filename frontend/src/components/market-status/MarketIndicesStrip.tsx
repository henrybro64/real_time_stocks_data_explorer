import { InlineLoading, Tag, Tile } from "@carbon/react";
import type { MarketIndex } from "@shared/api";
import { MARKET_INDEX_CATALOG } from "@shared/marketIndexCatalog";
import { useLanguage, type AppLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind, type RequestNoticeKind } from "../state/RequestStateNotice";
import { formatLastUpdated, formatPercent } from "../../services/format/quote";
import { priceToneClassForMarket } from "../../services/market-data/priceTone";

type Props = {
  error?: unknown;
  isLoading?: boolean;
  items: MarketIndex[];
  onRetry?: () => void;
};

function isUnavailable(item: MarketIndex): boolean {
  return item.status === "unavailable" || item.value == null;
}

function catalogSubtitle(language: AppLanguage): string {
  return MARKET_INDEX_CATALOG.map((entry) =>
    language === "zh-TW" ? entry.labelZhTw : entry.label,
  ).join(" / ");
}

function sessionTagType(item: MarketIndex, unavailable: boolean): "green" | "gray" {
  if (unavailable || !item.isMarketOpen) {
    return "gray";
  }
  return "green";
}

function sessionTagLabel(
  item: MarketIndex,
  unavailable: boolean,
  copy: { live: string; closed: string; latest: string; unavailable: string },
): string {
  if (unavailable) {
    return copy.unavailable;
  }
  if (item.market === "COMMODITY") {
    return copy.latest;
  }
  return item.isMarketOpen ? copy.live : copy.closed;
}

function sectionNoticeKind(
  error: unknown,
  resolvedItems: MarketIndex[],
): RequestNoticeKind | null {
  if (error) {
    const hasUsableData = resolvedItems.some((item) => !isUnavailable(item));
    return hasUsableData ? "stale" : errorToNoticeKind(error);
  }
  // Partial provider failures are shown on the cards; only escalate when every slot failed.
  if (resolvedItems.length > 0 && resolvedItems.every(isUnavailable)) {
    return "unavailable";
  }
  return null;
}

/** Keep a stable catalog layout even if the API omits a slot. */
export function resolveMarketIndexSlots(items: MarketIndex[]): MarketIndex[] {
  const byId = new Map(items.map((item) => [item.id, item]));
  return MARKET_INDEX_CATALOG.map((slot) => {
    const existing = byId.get(slot.id);
    if (existing) {
      return existing;
    }
    return {
      id: slot.id,
      market: slot.market,
      label: slot.label,
      value: null,
      change: null,
      changePercent: null,
      timestamp: new Date(0).toISOString(),
      isMarketOpen: false,
      status: "unavailable",
      unavailableReason: "missing_from_response",
    };
  });
}

export function MarketIndicesStrip({ error, isLoading = false, items, onRetry }: Props) {
  const { language } = useLanguage();
  const copy =
    language === "zh-TW"
      ? {
          eyebrow: "最新市場價格",
          title: "最新市場價格",
          subtitle: catalogSubtitle(language),
          loading: "載入市場價格中",
          live: "開盤中",
          closed: "已收盤",
          latest: "最新",
          unavailable: "暫時無法取得",
          updated: "更新",
          emDash: "—",
        }
      : {
          eyebrow: "Latest market prices",
          title: "Latest Market Prices",
          subtitle: catalogSubtitle(language),
          loading: "Loading market prices",
          live: "Live",
          closed: "Closed",
          latest: "Latest",
          unavailable: "Temporarily unavailable",
          updated: "Updated",
          emDash: "—",
        };
  const numberFormat = new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const signedNumberFormat = new Intl.NumberFormat(language === "zh-TW" ? "zh-TW" : "en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "always",
  });
  // Cold start: HomePage passes [] while the first request is in flight. Do not invent
  // unavailable placeholders or outage notices until a response (or error) exists.
  const isInitialLoad = Boolean(isLoading && items.length === 0 && !error);
  const resolvedItems =
    isInitialLoad || (Boolean(error) && items.length === 0)
      ? []
      : resolveMarketIndexSlots(items);
  const noticeKind = isInitialLoad ? null : sectionNoticeKind(error, resolvedItems);

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{copy.eyebrow}</p>
          <h2>{copy.title}</h2>
        </div>
        <p className="app-section-meta">{copy.subtitle}</p>
      </div>
      {noticeKind ? (
        <div className="app-state-notice-wrap">
          <RequestStateNotice kind={noticeKind} onRetry={onRetry} scopeLabel={copy.title} />
        </div>
      ) : null}
      {isLoading ? <InlineLoading description={copy.loading} status="active" /> : null}
      <div className="app-market-grid">
        {resolvedItems.map((item) => {
          const unavailable = isUnavailable(item);
          return (
            <Tile key={item.id} className="app-subtile" data-testid={`market-index-card-${item.id}`}>
              <div className="app-market-card-top">
                <strong>{item.label}</strong>
                <Tag size="sm" type={sessionTagType(item, unavailable)}>
                  {sessionTagLabel(item, unavailable, copy)}
                </Tag>
              </div>
              {unavailable ? (
                <>
                  <p className="app-stock-card-price">{copy.emDash}</p>
                  <div className="app-state-notice-wrap">
                    <RequestStateNotice compact kind="unavailable" scopeLabel={item.label} />
                  </div>
                </>
              ) : (
                <>
                  <p className="app-stock-card-price">{numberFormat.format(item.value ?? 0)}</p>
                  {item.change == null || item.changePercent == null ? (
                    // No trustworthy previous close (e.g. price-only fallback): never show a false 0.00%.
                    <p className="app-stock-card-change" data-testid={`market-index-change-${item.id}`}>
                      {copy.emDash}
                    </p>
                  ) : (
                    <p
                      className={`${priceToneClassForMarket(item.market, item.change)} app-stock-card-change`}
                      data-testid={`market-index-change-${item.id}`}
                    >
                      {signedNumberFormat.format(item.change)} ({formatPercent(item.changePercent)})
                    </p>
                  )}
                  <p className="app-section-meta">
                    {copy.updated} {formatLastUpdated(item.timestamp)}
                  </p>
                </>
              )}
            </Tile>
          );
        })}
      </div>
    </Tile>
  );
}
