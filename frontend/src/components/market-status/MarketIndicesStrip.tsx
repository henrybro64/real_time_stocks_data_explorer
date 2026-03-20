import { InlineLoading, Tag, Tile } from "@carbon/react";
import type { MarketIndex } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { formatLastUpdated, formatPercent } from "../../services/format/quote";

type Props = {
  isLoading?: boolean;
  items: MarketIndex[];
};

export function MarketIndicesStrip({ isLoading = false, items }: Props) {
  const { language } = useLanguage();
  const copy =
    language === "zh-TW"
      ? {
          eyebrow: "最新指數",
          title: "最新市場指數",
          subtitle: "台灣證券交易所 / 道瓊工業 / 那斯達克 / 標普 500",
          loading: "載入市場指數中",
          live: "交易中",
          closed: "已收盤",
          updated: "更新",
        }
      : {
          eyebrow: "Latest market prices",
          title: "Latest Market Prices",
          subtitle: "Taiwan Stock Exchange / DOW Jones / Nasdaq / S&P 500",
          loading: "Loading market indices",
          live: "Live",
          closed: "Closed",
          updated: "Updated",
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

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{copy.eyebrow}</p>
          <h2>{copy.title}</h2>
        </div>
        <p className="app-section-meta">{copy.subtitle}</p>
      </div>
      {isLoading ? <InlineLoading description={copy.loading} status="active" /> : null}
      <div className="app-market-grid">
        {items.map((item) => (
          <Tile key={item.id} className="app-subtile">
            <div className="app-market-card-top">
              <strong>{item.label}</strong>
              <Tag size="sm" type={item.isMarketOpen ? "green" : "gray"}>
                {item.isMarketOpen ? copy.live : copy.closed}
              </Tag>
            </div>
            <p className="app-stock-card-price">{numberFormat.format(item.value)}</p>
            <p className={item.change >= 0 ? "app-positive app-stock-card-change" : "app-negative app-stock-card-change"}>
              {signedNumberFormat.format(item.change)} ({formatPercent(item.changePercent)})
            </p>
            <p className="app-section-meta">
              {copy.updated} {formatLastUpdated(item.timestamp)}
            </p>
          </Tile>
        ))}
      </div>
    </Tile>
  );
}
