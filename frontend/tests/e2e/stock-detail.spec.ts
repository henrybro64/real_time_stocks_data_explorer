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
  await expect(page.getByTestId("tw-margin-short-summary")).toContainText("31,339 張");
  await expect(page.getByTestId("tw-margin-short-summary")).toContainText("0 張");
  await expect(page.getByTestId("tw-margin-short-summary")).toContainText("+141 張");
  await expect(page.getByTestId("tw-margin-short-summary")).toContainText("+2,609 萬");
  await expect(page.getByLabel("融資餘額(張)")).toBeVisible();
  await expect(page.getByLabel("融券餘額(張)")).toBeVisible();
  await expect(page.getByLabel("借券賣出餘額增減張數")).toBeVisible();
  await expect(page.getByLabel("借券賣出餘額增減金額(萬)")).toBeVisible();

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

test("TW institutional section shows loading then summary", async ({ page }) => {
  let releaseResponse: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });

  await page.route("**/api/stocks/*/institutional-investors*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await gate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        stockId: "TW:2330",
        symbol: "2330",
        market: "TWSE",
        latestDate: "2026-07-06",
        summary: {
          foreignNetLots: 12345,
          investmentTrustNetLots: 1200,
          dealerNetLots: -800,
          totalNetLots: 12745,
        },
        history: [
          {
            date: "2026-07-06",
            foreignNetLots: 12345,
            investmentTrustNetLots: 1200,
            dealerNetLots: -800,
            totalNetLots: 12745,
          },
        ],
        source: "TWSE T86 daily institutional data",
      }),
    });
  });

  await page.goto("/stock/TW%3A2330");
  await expect(page.getByTestId("tw-stock-signals-section")).toBeVisible();
  await expect(page.getByText("載入法人動態中")).toBeVisible();

  releaseResponse?.();

  await expect(page.getByTestId("tw-institutional-summary")).toContainText("+12,345 張");
  await expect(page.getByLabel("外資買賣超(張)")).toBeVisible();
});

test("TW margin-short section shows empty state when API returns no data", async ({ page }) => {
  await page.route("**/api/stocks/*/margin-short*", async (route) => {
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
        source: "TWSE official margin and borrowed-short daily data",
      }),
    });
  });

  await page.goto("/stock/TW%3A2330");
  const signals = page.getByTestId("tw-stock-signals-section");
  await signals.getByRole("tab", { name: "資券變化" }).click();
  await expect(page.getByTestId("tw-stock-signals-margin-short-panel")).toContainText(
    "目前沒有可顯示的資券資料。",
  );
  await expect(page.getByTestId("tw-margin-short-summary")).toHaveCount(0);
});

test("TW margin-short section shows error notice when API fails", async ({ page }) => {
  await page.route("**/api/stocks/*/margin-short*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Taiwan margin-short data is temporarily unavailable from official sources.",
      }),
    });
  });

  await page.goto("/stock/TW%3A2330");
  const signals = page.getByTestId("tw-stock-signals-section");
  await signals.getByRole("tab", { name: "資券變化" }).click();
  await expect(page.getByText("資料來源暫時不可用")).toBeVisible();
  await expect(page.getByTestId("tw-margin-short-summary")).toHaveCount(0);
});

test("TW margin-short section shows loading then summary", async ({ page }) => {
  let releaseResponse: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });

  await page.route("**/api/stocks/*/margin-short*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await gate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        stockId: "TW:2330",
        symbol: "2330",
        market: "TWSE",
        latestDate: "2026-07-06",
        summary: {
          marginBalanceLots: 31339,
          shortMarginBalanceLots: 0,
          borrowedShortBalanceLots: 12345,
          borrowedShortBalanceChangeLots: 141,
          borrowedShortBalanceChangeAmount10k: 2609,
        },
        history: [
          {
            date: "2026-07-06",
            closePrice: 185,
            marginBalanceLots: 31339,
            shortMarginBalanceLots: 0,
            borrowedShortBalanceLots: 12345,
            borrowedShortBalanceChangeLots: 141,
            borrowedShortBalanceChangeAmount10k: 2609,
          },
        ],
        source: "TWSE official margin and borrowed-short daily data",
      }),
    });
  });

  await page.goto("/stock/TW%3A2330");
  const signals = page.getByTestId("tw-stock-signals-section");
  await signals.getByRole("tab", { name: "資券變化" }).click();
  await expect(page.getByText("載入資券變化中")).toBeVisible();

  releaseResponse?.();

  await expect(page.getByTestId("tw-margin-short-summary")).toContainText("31,339 張");
  await expect(page.getByLabel("融資餘額(張)")).toBeVisible();
});
