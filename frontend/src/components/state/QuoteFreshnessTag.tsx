"use client";

import { Tag } from "@carbon/react";
import type { QuoteFreshnessMeta } from "@shared/quote";
import { useLanguage } from "../../app/i18n";
import { getQuoteFreshness, type QuoteFreshness } from "../../services/market-data/freshness";

type Props = {
  className?: string;
  isMarketOpen: boolean;
  refreshFailed?: boolean;
  timestamp: string;
  freshnessMeta?: QuoteFreshnessMeta | null;
  hasQuote?: boolean;
};

export function QuoteFreshnessTag({
  className = "",
  isMarketOpen,
  refreshFailed = false,
  timestamp,
  freshnessMeta = null,
  hasQuote = true,
}: Props) {
  const { language } = useLanguage();
  const freshness = getQuoteFreshness(timestamp, isMarketOpen, {
    refreshFailed,
    meta: freshnessMeta,
    hasQuote,
  });

  const copy =
    language === "zh-TW"
      ? {
          live: "即時",
          delayed: "延遲",
          stale: "過舊",
          closed: "已收盤",
          refreshing: "更新中",
          unavailable: "無法取得",
        }
      : {
          live: "Live",
          delayed: "Delayed",
          stale: "Stale",
          closed: "Closed",
          refreshing: "Updating",
          unavailable: "Unavailable",
        };

  return (
    <Tag className={className} data-testid="quote-freshness-tag" size="sm" type={tagTypeFor(freshness)}>
      {copy[freshness]}
    </Tag>
  );
}

function tagTypeFor(freshness: QuoteFreshness): "green" | "cool-gray" | "red" | "gray" | "magenta" {
  switch (freshness) {
    case "live":
      return "green";
    case "delayed":
      return "cool-gray";
    case "stale":
      return "red";
    case "refreshing":
      return "magenta";
    case "unavailable":
      return "red";
    default:
      return "gray";
  }
}
