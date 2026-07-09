import { expect, test } from "./setup";

import { openHome, searchFromHome, TW_STOCK_ID } from "./helpers";


test("stock detail renders quote, history, key stats, news fallback, and Taiwan-only insights", async ({ page }) => {
  await openHome(page);
  const searchCard = await searchFromHome(page);

  await searchCard.getByTestId(`stock-card-view-${TW_STOCK_ID}`).click();

  await expect(page).toHaveURL(/\/stock\/TW(%3A|:)2330$/);
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  await expect(page.getByTestId("quote-summary-section")).toContainText("2330");
  await expect(page.getByTestId("quote-summary-section")).toContainText("台積電");
  const keyStats = page.getByTestId("key-statistics-section");
  await expect(keyStats).toBeVisible();
  await expect(keyStats).toContainText("20.00x");
  await expect(page.getByTestId("stock-news-section")).toBeVisible();
  await expect(page.getByTestId("tw-insights-section")).toBeVisible();
  await expect(page.getByTestId("us-insights-section")).toHaveCount(0);

  const signals = page.getByTestId("tw-stock-signals-section");
  await expect(signals).toBeVisible();
  await expect(signals.getByRole("tab", { name: "法人動態" })).toBeVisible();
  await expect(signals.getByRole("tab", { name: "資券變化" })).toBeVisible();
  await expect(page.getByTestId("tw-stock-signals-institutional-panel")).toContainText("三大法人買賣超");
  await signals.getByRole("tab", { name: "資券變化" }).click();
  await expect(page.getByTestId("tw-stock-signals-margin-short-panel")).toContainText("資券變化");

  await page.getByRole("tab", { name: "1W" }).click();
  await expect(page.getByRole("tab", { name: "1W", selected: true })).toBeVisible();
});

test("stock detail route is bookmarkable and reload-safe", async ({ page }) => {
  await page.goto("/stock/TW%3A2330");
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  await expect(page.getByTestId("tw-stock-signals-section")).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(/\/stock\/TW(%3A|:)2330$/);
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  await expect(page.getByTestId("tw-stock-signals-section")).toBeVisible();
});

