import { InlineLoading, Link, Tile } from "@carbon/react";
import type { NewsItem } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { formatLastUpdated } from "../../services/format/quote";

type Props = {
  items: NewsItem[];
  isLoading?: boolean;
};

export function StockNewsPanel({ items, isLoading = false }: Props) {
  const { language } = useLanguage();
  const copy =
    language === "zh-TW"
      ? {
          eyebrow: "最新新聞",
          title: "最近 24 小時相關新聞",
          hint: "點擊標題即可開啟原始新聞連結",
          loading: "載入新聞中",
          empty: "最近 24 小時暫無可顯示的相關新聞。",
          updated: "發布",
          source: "來源",
        }
      : {
          eyebrow: "Latest news",
          title: "Relevant News in Last 24 Hours",
          hint: "Click a headline to open the original news link.",
          loading: "Loading news",
          empty: "No relevant news in the last 24 hours.",
          updated: "Published",
          source: "Source",
        };

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">{copy.eyebrow}</p>
          <h2>{copy.title}</h2>
        </div>
        <p className="app-section-meta">{copy.hint}</p>
      </div>
      {isLoading ? <InlineLoading description={copy.loading} status="active" /> : null}
      {items.length === 0 && !isLoading ? (
        <p className="app-supporting-copy">{copy.empty}</p>
      ) : (
        <div className="app-news-list">
          {items.map((item) => (
            <Tile className="app-subtile" key={`${item.url}-${item.publishedAt}`}>
              <Link href={item.url} target="_blank" rel="noreferrer">
                {item.title}
              </Link>
              <p className="app-section-meta">
                {copy.source}: {item.source}
              </p>
              <p className="app-section-meta">
                {copy.updated} {formatLastUpdated(item.publishedAt)}
              </p>
            </Tile>
          ))}
        </div>
      )}
    </Tile>
  );
}
