"use client";

import { Tag } from "@carbon/react";
import { useLanguage } from "../../app/i18n";
import { getQuoteFreshness } from "../../services/market-data/freshness";

type Props = {
  className?: string;
  isMarketOpen: boolean;
  refreshFailed?: boolean;
  timestamp: string;
};

export function QuoteFreshnessTag({
  className = "",
  isMarketOpen,
  refreshFailed = false,
  timestamp,
}: Props) {
  const { language } = useLanguage();
  const freshness = getQuoteFreshness(timestamp, isMarketOpen, { refreshFailed });

  const copy =
    language === "zh-TW"
      ? {
          live: "即時",
          delayed: "延遲",
          stale: "過舊",
          closed: "已收盤",
        }
      : {
          live: "Live",
          delayed: "Delayed",
          stale: "Stale",
          closed: "Closed",
        };

  const tagType =
    freshness === "live"
      ? "green"
      : freshness === "delayed"
        ? "cool-gray"
        : freshness === "stale"
          ? "red"
          : "gray";

  return (
    <Tag className={className} data-testid="quote-freshness-tag" size="sm" type={tagType}>
      {copy[freshness]}
    </Tag>
  );
}
