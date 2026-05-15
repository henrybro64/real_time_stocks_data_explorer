import { expect, test } from "./setup";

import {
  addTwStockFromSearchCard,
  ensurePortfolioDoesNotContain,
  expectPortfolioPosition,
  TW_STOCK_ID,
} from "./helpers";

const AI_NOT_CONFIGURED_DETAIL =
  "AI is not configured. Please set up your AI provider in AI Settings.";

const ANALYSIS_RESPONSE_TIMEOUT_MS = process.env.CI ? 30_000 : 15_000;


test("portfolio AI analysis shows not-configured guidance when AI is unset", async ({ page }) => {
  await page.route("**/api/portfolio/analysis", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ detail: AI_NOT_CONFIGURED_DETAIL }),
    });
  });

  await ensurePortfolioDoesNotContain(page, TW_STOCK_ID);

  await addTwStockFromSearchCard(page, "portfolio-search-section");
  await expectPortfolioPosition(page);

  await page.getByTestId("portfolio-ai-analyze-button").click();

  await expect(page.getByTestId("portfolio-ai-not-configured")).toBeVisible({
    timeout: ANALYSIS_RESPONSE_TIMEOUT_MS,
  });
  await expect(page.getByTestId("portfolio-ai-settings-link")).toBeVisible();
});
