import { InlineLoading, Tag, Tile } from "@carbon/react";
import type { MarketStatus } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { formatMarketTime } from "../../services/format/quote";

type Props = {
  isLoading?: boolean;
  markets: MarketStatus[];
};

export function MarketStatusStrip({ isLoading = false, markets }: Props) {
  const { t } = useLanguage();

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Market status</p>
          <h2>{t("marketStatus.title")}</h2>
        </div>
        <p className="app-section-meta">{t("marketStatus.syncCount", { count: markets.length })}</p>
      </div>
      {isLoading ? <InlineLoading description={t("marketStatus.loading")} status="active" /> : null}
      <div className="app-market-grid">
        {markets.map((market) => (
          <Tile key={market.market} className="app-subtile">
            <div className="app-market-card-top">
              <strong>{market.label}</strong>
              <Tag size="sm" type={market.isOpen ? "green" : "gray"}>
                {market.isOpen ? t("marketStatus.open") : t("marketStatus.closed")}
              </Tag>
            </div>
            <p className="app-supporting-copy">{formatMarketTime(market.localTime, market.timezone)}</p>
          </Tile>
        ))}
      </div>
    </Tile>
  );
}
