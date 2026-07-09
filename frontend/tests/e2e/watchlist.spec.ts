import { expect, test } from "./setup";

import {
  addTwStockFromSearchCard,
  ensureWatchlistDoesNotContain,
  expectWatchlistCard,
  openHome,
  TW_STOCK_ID,
} from "./helpers";


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

