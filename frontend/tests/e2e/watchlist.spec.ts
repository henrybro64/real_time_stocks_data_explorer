import { expect, test } from "./setup";

import {
  addTwStockFromSearchCard,
  ensureWatchlistDoesNotContain,
  expectWatchlistCard,
  openHome,
  TW_STOCK_ID,
} from "./helpers";

test("watchlist quotes do not poll every 2 seconds and pause while hidden", async ({ page }) => {
  test.setTimeout(60_000);

  const quoteBatchTimestamps: number[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/quotes?") && request.method() === "GET") {
      quoteBatchTimestamps.push(Date.now());
    }
  });

  await ensureWatchlistDoesNotContain(page, TW_STOCK_ID);
  await openHome(page);
  await addTwStockFromSearchCard(page, "stock-search-section");
  await expectWatchlistCard(page);

  await expect
    .poll(() => quoteBatchTimestamps.length, { timeout: 15_000 })
    .toBeGreaterThanOrEqual(1);
  const afterInitial = quoteBatchTimestamps.length;

  // Open cadence is 45s; ~5s must not look like the legacy 2s poller.
  await page.waitForTimeout(5_000);
  expect(quoteBatchTimestamps.length).toBe(afterInitial);

  const beforeHidden = quoteBatchTimestamps.length;
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(5_000);
  expect(quoteBatchTimestamps.length).toBe(beforeHidden);
});

test("watchlist add persists across refresh and can be removed cleanly", async ({ page }) => {
  await ensureWatchlistDoesNotContain(page, TW_STOCK_ID);
  await openHome(page);
  await addTwStockFromSearchCard(page, "stock-search-section");
  await expectWatchlistCard(page);

  await page.reload();
  await openHome(page);
  await expect(page.getByTestId("watchlist-section").getByTestId(`stock-card-${TW_STOCK_ID}`)).toBeVisible();

  await page
    .getByTestId("watchlist-section")
    .getByTestId(`stock-card-${TW_STOCK_ID}`)
    .getByTestId(`stock-card-toggle-${TW_STOCK_ID}`)
    .click();
  await expect(page.getByTestId("watchlist-section").getByTestId(`stock-card-${TW_STOCK_ID}`)).toHaveCount(0);
});

test("watchlist stock card click opens the stock detail page", async ({ page }) => {
  await ensureWatchlistDoesNotContain(page, TW_STOCK_ID);
  await openHome(page);
  await addTwStockFromSearchCard(page, "stock-search-section");
  await expectWatchlistCard(page);

  await page.getByTestId("watchlist-section").getByTestId(`stock-card-${TW_STOCK_ID}`).click();
  await expect(page).toHaveURL(/\/stock\/TW(%3A|:)2330$/);
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  await expect(page.getByTestId("tw-stock-signals-section")).toBeVisible();
});

