import { expect, Page } from "@playwright/test";


export const TW_STOCK_ID = "TW:2330";
export const TW_STOCK_SYMBOL = "2330";

function isSearchResponse(urlString: string, query: string) {
  const url = new URL(urlString);
  return url.pathname.endsWith("/api/search") && url.searchParams.get("q") === query;
}

async function waitForSearchResult(
  page: Page,
  inputSelector: string,
  sectionTestId: string,
  query: string,
) {
  const searchSection = page.getByTestId(sectionTestId);
  const responsePromise = page.waitForResponse(
    (response) => response.ok() && isSearchResponse(response.url(), query),
    { timeout: 10_000 },
  );
  await page.locator(inputSelector).fill(query);
  await responsePromise;
  await expect(searchSection.getByTestId(`stock-card-${TW_STOCK_ID}`)).toBeVisible({ timeout: 10_000 });
  return searchSection.getByTestId(`stock-card-${TW_STOCK_ID}`);
}


export async function openHome(page: Page) {
  await page.goto("/");
  await expect(page.locator("#main-content")).toBeVisible();
}


export async function searchFromHome(page: Page, query = TW_STOCK_SYMBOL) {
  return waitForSearchResult(page, "#stock-search", "stock-search-section", query);
}


export async function searchFromPortfolio(page: Page, query = TW_STOCK_SYMBOL) {
  await page.goto("/portfolio");
  await expect(page.locator("#main-content")).toBeVisible();
  return waitForSearchResult(page, "#portfolio-stock-search", "portfolio-search-section", query);
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
