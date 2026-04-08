import { expect, test } from "@playwright/test";

import { ensureWatchlistDoesNotContain, openHome, searchFromHome, TW_STOCK_ID } from "./helpers";


test("watchlist add persists across refresh and can be removed cleanly", async ({ page }) => {
  await ensureWatchlistDoesNotContain(page, TW_STOCK_ID);
  const searchCard = await searchFromHome(page);

  await searchCard.getByTestId(`stock-card-toggle-${TW_STOCK_ID}`).click();

  const watchlistSection = page.getByTestId("watchlist-section");
  const watchlistCard = watchlistSection.getByTestId(`stock-card-${TW_STOCK_ID}`);
  await expect(watchlistCard).toBeVisible();

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
