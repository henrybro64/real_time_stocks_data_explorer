import { expect, Page } from "@playwright/test";


export const TW_STOCK_ID = "TW:2330";
export const TW_STOCK_SYMBOL = "2330";

/** Home search uses React `useDeferredValue`; portfolio search is immediate. Allow slow CI. */
const SEARCH_RESULT_TIMEOUT_MS = process.env.CI ? 75_000 : 25_000;

async function waitForSearchResult(page: Page, sectionTestId: string, query: string) {
  const searchSection = page.getByTestId(sectionTestId);
  const card = searchSection.getByTestId(`stock-card-${TW_STOCK_ID}`);

  await expect(searchSection).toBeVisible({ timeout: 30_000 });
  await searchSection.scrollIntoViewIfNeeded();
  const input = searchSection.getByRole("searchbox");
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.click();
  await input.fill(query);
  await expect(card).toBeVisible({ timeout: SEARCH_RESULT_TIMEOUT_MS });

  return card;
}


export async function openHome(page: Page) {
  await page.goto("/");
  await expect(page.locator("#main-content")).toBeVisible();
  await page.waitForLoadState("load");
}


export async function searchFromHome(page: Page, query = TW_STOCK_SYMBOL) {
  return waitForSearchResult(page, "stock-search-section", query);
}


export async function searchFromPortfolio(page: Page, query = TW_STOCK_SYMBOL) {
  await page.goto("/portfolio");
  await expect(page.locator("#main-content")).toBeVisible();
  await page.waitForLoadState("load");
  return waitForSearchResult(page, "portfolio-search-section", query);
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
  await page.goto("/portfolio");
  await expect(page.locator("#main-content")).toBeVisible();
  const row = page.getByTestId(`portfolio-position-${stockId}`);
  if ((await row.count()) === 0) {
    return;
  }
  await row.getByTestId(`portfolio-remove-${stockId}`).click();
  await expect(row).toHaveCount(0);
}
