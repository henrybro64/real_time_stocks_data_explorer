import { InlineLoading, Tag, Tile } from "@carbon/react";
import type { MarketStatus } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../state/RequestStateNotice";
import { formatMarketTime } from "../../services/format/quote";

type Props = {
  error?: unknown;
  isLoading?: boolean;
  markets: MarketStatus[];
  onRetry?: () => void;
};

export function MarketStatusStrip({ error, isLoading = false, markets, onRetry }: Props) {
  const { t } = useLanguage();
  const noticeKind = error ? (markets.length > 0 ? "stale" : errorToNoticeKind(error)) : null;

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">Market status</p>
          <h2>{t("marketStatus.title")}</h2>
        </div>
        <p className="app-section-meta">{t("marketStatus.syncCount", { count: markets.length })}</p>
      </div>
      {noticeKind ? (
        <div className="app-state-notice-wrap">
          <RequestStateNotice kind={noticeKind} onRetry={onRetry} scopeLabel={t("marketStatus.title")} />
        </div>
      ) : null}
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
