export const portfolioMarketSectionMessages = {
  en: {
    "portfolio.marketSection.tw": "Taiwan Holdings",
    "portfolio.marketSection.us": "U.S. Holdings",
    "portfolio.marketSection.other": "Other Holdings",
    "portfolio.marketSection.count": "{count} holdings",
  },
  "zh-TW": {
    "portfolio.marketSection.tw": "台灣股票",
    "portfolio.marketSection.us": "美國股票",
    "portfolio.marketSection.other": "其他市場持股",
    "portfolio.marketSection.count": "{count} 筆持股",
  },
} as const;

export type PortfolioMarketSectionTranslationKey =
  keyof (typeof portfolioMarketSectionMessages)["en"];
