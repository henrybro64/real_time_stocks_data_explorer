import { expect, Page } from "@playwright/test";

import { installE2eApiMocks } from "./e2eApiMocks";

export const TW_STOCK_ID = "TW:2330";
export const TW_STOCK_SYMBOL = "2330";
export const TPEX_STOCK_ID = "TW:6488";
export const US_STOCK_ID = "US:AAPL";
const US_STOCK_SYMBOL = "AAPL";

export const E2E_TW_STOCK_SEARCH_RESULT = {
  id: TW_STOCK_ID,
  market: "TW",
  symbol: TW_STOCK_SYMBOL,
  name: "台積電",
  nameEn: "TSMC",
  nameZhTw: "台積電",
  exchange: "TWSE",
  currency: "TWD",
  timezone: "Asia/Taipei",
} as const;

export const E2E_US_STOCK_SEARCH_RESULT = {
  id: US_STOCK_ID,
  market: "US",
  symbol: US_STOCK_SYMBOL,
  name: "Apple Inc.",
  nameEn: "Apple Inc.",
  nameZhTw: null,
  exchange: "NASDAQ",
  currency: "USD",
  timezone: "America/New_York",
} as const;

/** Home search uses React `useDeferredValue`; portfolio search is immediate. Allow slow CI. */
const SEARCH_RESULT_TIMEOUT_MS = process.env.CI ? 75_000 : 25_000;
const POSITION_ROW_TIMEOUT_MS = process.env.CI ? 30_000 : 15_000;
const MOCKS_ONLY = process.env.PLAYWRIGHT_MOCKS_ONLY === "1";

async function waitForBackendHealth(page: Page): Promise<void> {
  if (MOCKS_ONLY) {
    return;
  }
  const attempts = process.env.CI ? 60 : 20;
  for (let index = 0; index < attempts; index += 1) {
    try {
      const response = await page.request.get("http://127.0.0.1:8000/health");
      if (response.ok()) {
        return;
      }
    } catch {
      // Backend may still be starting between Playwright tests.
    }
    await page.waitForTimeout(1_000);
  }
  throw new Error("Backend health check did not succeed in time.");
}

async function waitForSearchResult(page: Page, sectionTestId: string, query: string) {
  await installE2eApiMocks(page);
  await waitForBackendHealth(page);

  const searchSection = page.getByTestId(sectionTestId);
  const card = searchSection.getByTestId(`stock-card-${TW_STOCK_ID}`);

  await expect(searchSection).toBeVisible({ timeout: 30_000 });
  const input = searchSection.getByRole("searchbox");
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.scrollIntoViewIfNeeded();
  await input.click();
  await input.fill(query);
  await expect(card).toBeVisible({ timeout: SEARCH_RESULT_TIMEOUT_MS });

  return card;
}

export async function addTwStockFromSearchCard(page: Page, sectionTestId: string) {
  const card = await waitForSearchResult(page, sectionTestId, TW_STOCK_SYMBOL);
  await card.getByTestId(`stock-card-toggle-${TW_STOCK_ID}`).click();
  return card;
}

export async function expectPortfolioPosition(page: Page, stockId = TW_STOCK_ID) {
  await expect(page.getByTestId(`portfolio-position-${stockId}`)).toBeVisible({
    timeout: POSITION_ROW_TIMEOUT_MS,
  });
}

export async function expectWatchlistCard(page: Page, stockId = TW_STOCK_ID) {
  const watchlistSection = page.getByTestId("watchlist-section");
  await expect(watchlistSection.getByTestId(`stock-card-${stockId}`)).toBeVisible({
    timeout: POSITION_ROW_TIMEOUT_MS,
  });
}

export async function openHome(page: Page) {
  await installE2eApiMocks(page);
  await waitForBackendHealth(page);
  await page.goto("/");
  await expect(page.locator("#main-content")).toBeVisible();
  await page.waitForLoadState("load");
}

export async function searchFromHome(page: Page, query = TW_STOCK_SYMBOL) {
  return waitForSearchResult(page, "stock-search-section", query);
}

export async function ensureWatchlistDoesNotContain(page: Page, stockId = TW_STOCK_ID) {
  await openHome(page);
  const watchlistSection = page.getByTestId("watchlist-section");
  const card = watchlistSection.getByTestId(`stock-card-${stockId}`);
  if ((await card.count()) === 0) {
    return;
  }
  await card.getByTestId(`stock-card-toggle-${stockId}`).click();
  await expect(card).toHaveCount(0);
}

export async function ensurePortfolioDoesNotContain(page: Page, stockId = TW_STOCK_ID) {
  await installE2eApiMocks(page);
  await waitForBackendHealth(page);
  await page.goto("/portfolio");
  await expect(page.locator("#main-content")).toBeVisible();
  const row = page.getByTestId(`portfolio-position-${stockId}`);
  if ((await row.count()) === 0) {
    return;
  }
  await row.getByTestId(`portfolio-remove-${stockId}`).click();
  await expect(row).toHaveCount(0);
}
