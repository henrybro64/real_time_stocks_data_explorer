import { expect, test } from "./setup";

import { openHome, searchFromHome, TW_STOCK_ID } from "./helpers";


test("stock detail renders quote, history, key stats, news fallback, and Taiwan-only insights", async ({ page }) => {
  await openHome(page);
  const searchCard = await searchFromHome(page);

  await searchCard.getByTestId(`stock-card-view-${TW_STOCK_ID}`).click();

  await expect(page).toHaveURL(/\/stock\/TW(%3A|:)2330$/);
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  await expect(page.getByTestId("key-statistics-section")).toBeVisible();
  await expect(page.getByTestId("stock-news-section")).toBeVisible();
  await expect(page.getByTestId("tw-insights-section")).toBeVisible();
  await expect(page.getByTestId("us-insights-section")).toHaveCount(0);

  await page.getByRole("tab", { name: "1W" }).click();
  await expect(page.getByRole("tab", { name: "1W", selected: true })).toBeVisible();
});
