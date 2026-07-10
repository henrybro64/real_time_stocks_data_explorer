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
  await expect(page.getByTestId("tw-institutional-summary")).toContainText("+12,345 張");
  await expect(page.getByTestId("tw-institutional-summary")).toContainText("+1,200 張");
  await expect(page.getByTestId("tw-institutional-summary")).toContainText("-800 張");
  await expect(page.getByTestId("tw-institutional-summary")).toContainText("+12,745 張");
  await expect(page.getByLabel("外資買賣超(張)")).toBeVisible();
  await expect(page.getByLabel("投信買賣超(張)")).toBeVisible();
  await expect(page.getByLabel("自營商買賣超(張)")).toBeVisible();
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

test("TW institutional section shows empty state when API returns no data", async ({ page }) => {
  await page.route("**/api/stocks/*/institutional-investors*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        stockId: "TW:2330",
        symbol: "2330",
        market: "TWSE",
        latestDate: null,
        summary: null,
        history: [],
        source: "TWSE T86 daily institutional data",
      }),
    });
  });

  await page.goto("/stock/TW%3A2330");
  await expect(page.getByTestId("tw-stock-signals-section")).toBeVisible();
  await expect(page.getByTestId("tw-stock-signals-institutional-panel")).toContainText(
    "目前沒有可顯示的三大法人資料。",
  );
  await expect(page.getByTestId("tw-institutional-summary")).toHaveCount(0);
});

test("TW institutional section shows error notice when API fails", async ({ page }) => {
  await page.route("**/api/stocks/*/institutional-investors*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Taiwan institutional data is temporarily unavailable from official sources.",
      }),
    });
  });

  await page.goto("/stock/TW%3A2330");
  await expect(page.getByTestId("tw-stock-signals-section")).toBeVisible();
  await expect(page.getByText("資料來源暫時不可用")).toBeVisible();
  await expect(page.getByTestId("tw-institutional-summary")).toHaveCount(0);
});
