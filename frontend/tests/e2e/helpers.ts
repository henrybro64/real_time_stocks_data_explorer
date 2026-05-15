import { expect, Page, Route } from "@playwright/test";


export const TW_STOCK_ID = "TW:2330";
export const TW_STOCK_SYMBOL = "2330";

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

/** Home search uses React `useDeferredValue`; portfolio search is immediate. Allow slow CI. */
const SEARCH_RESULT_TIMEOUT_MS = process.env.CI ? 75_000 : 25_000;

const pagesWithSearchMock = new WeakSet<Page>();

export async function installE2eSearchMock(page: Page): Promise<void> {
  if (pagesWithSearchMock.has(page)) {
    return;
  }
  pagesWithSearchMock.add(page);

  await page.route(/\/api\/search(\?|$)/, async (route: Route) => {
    const url = new URL(route.request().url());
    const query = (url.searchParams.get("q") ?? "").trim();
    if (!query) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
      return;
    }

    const normalized = query.toLowerCase();
    const matchesTaiwanTsmc =
      normalized.includes("2330") ||
      normalized.includes("tsmc") ||
      normalized.includes("台積");

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(matchesTaiwanTsmc ? [E2E_TW_STOCK_SEARCH_RESULT] : []),
    });
  });
}

async function waitForBackendHealth(page: Page): Promise<void> {
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
  await installE2eSearchMock(page);
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


export async function openHome(page: Page) {
  await installE2eSearchMock(page);
  await waitForBackendHealth(page);
  await page.goto("/");
  await expect(page.locator("#main-content")).toBeVisible();
  await page.waitForLoadState("load");
}


export async function searchFromHome(page: Page, query = TW_STOCK_SYMBOL) {
  return waitForSearchResult(page, "stock-search-section", query);
}


export async function searchFromPortfolio(page: Page, query = TW_STOCK_SYMBOL) {
  await installE2eSearchMock(page);
  await waitForBackendHealth(page);
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
  await installE2eSearchMock(page);
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
