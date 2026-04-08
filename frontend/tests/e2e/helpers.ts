import { expect, Page } from "@playwright/test";


export const TW_STOCK_ID = "TW:2330";
export const TW_STOCK_SYMBOL = "2330";


export async function openHome(page: Page) {
  await page.goto("/");
  await expect(page.locator("#main-content")).toBeVisible();
}


export async function searchFromHome(page: Page, query = TW_STOCK_SYMBOL) {
  const searchSection = page.getByTestId("stock-search-section");
  await page.locator("#stock-search").fill(query);
  await expect(searchSection.getByTestId(`stock-card-${TW_STOCK_ID}`)).toBeVisible();
  return searchSection.getByTestId(`stock-card-${TW_STOCK_ID}`);
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
  await row.getByRole("button", { name: /remove/i }).click();
  await expect(row).toHaveCount(0);
}
