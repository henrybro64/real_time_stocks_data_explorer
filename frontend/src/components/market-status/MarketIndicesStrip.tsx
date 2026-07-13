import { InlineLoading, Tag, Tile } from "@carbon/react";
import type { MarketIndex } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind, type RequestNoticeKind } from "../state/RequestStateNotice";
import { formatLastUpdated, formatPercent } from "../../services/format/quote";

type Props = {
  error?: unknown;
  isLoading?: boolean;
  items: MarketIndex[];
};

const EXPECTED_INDEX_SLOTS: ReadonlyArray<Pick<MarketIndex, "id" | "market" | "label">> = [
  { id: "TWSE:TAIEX", market: "TW", label: "Taiwan Stock Exchange" },
  { id: "^DJI", market: "US", label: "DOW Jones" },
  { id: "^NDQ", market: "US", label: "Nasdaq" },
  { id: "^SPX", market: "US", label: "S&P 500" },
];

function isUnavailable(item: MarketIndex): boolean {
  return item.status === "unavailable" || item.value == null;
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
  copy: { live: string; closed: string; unavailable: string },
): string {
  if (unavailable) {
    return copy.unavailable;
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

/** Keep a stable four-card layout even if the API omits a slot. */
export function resolveMarketIndexSlots(items: MarketIndex[]): MarketIndex[] {
  const byId = new Map(items.map((item) => [item.id, item]));
  return EXPECTED_INDEX_SLOTS.map((slot) => {
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

export function MarketIndicesStrip({ error, isLoading = false, items }: Props) {
  const { language } = useLanguage();
  const copy =
    language === "zh-TW"
      ? {
          eyebrow: "最新指數",
          title: "最新市場指數",
          subtitle: "台灣證券交易所 / 道瓊工業 / 那斯達克 / 標普 500",
          loading: "載入市場指數中",
          live: "開盤中",
          closed: "已收盤",
          unavailable: "暫時無法取得",
          updated: "更新",
          emDash: "—",
        }
      : {
          eyebrow: "Latest market prices",
          title: "Latest Market Prices",
          subtitle: "Taiwan Stock Exchange / DOW Jones / Nasdaq / S&P 500",
          loading: "Loading market indices",
          live: "Live",
          closed: "Closed",
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
  const resolvedItems = resolveMarketIndexSlots(items);
  const noticeKind = sectionNoticeKind(error, resolvedItems);

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
          <RequestStateNotice kind={noticeKind} scopeLabel={copy.title} />
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
                  <p
                    className={
                      (item.change ?? 0) >= 0
                        ? "app-positive app-stock-card-change"
                        : "app-negative app-stock-card-change"
                    }
                  >
                    {signedNumberFormat.format(item.change ?? 0)} ({formatPercent(item.changePercent ?? 0)})
                  </p>
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
