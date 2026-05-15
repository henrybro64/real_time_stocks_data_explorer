import { expect, test } from "@playwright/test";

import {
  ensurePortfolioDoesNotContain,
  searchFromPortfolio,
  TW_STOCK_ID,
} from "./helpers";


test("portfolio AI analysis shows not-configured guidance when AI is unset", async ({ page }) => {
  await ensurePortfolioDoesNotContain(page, TW_STOCK_ID);

  const stockCard = await searchFromPortfolio(page);
  await stockCard.getByTestId(`stock-card-toggle-${TW_STOCK_ID}`).click();

  await expect(page.getByTestId(`portfolio-position-${TW_STOCK_ID}`)).toBeVisible();

  await page.getByTestId("portfolio-ai-analyze-button").click();

  await expect(page.getByTestId("portfolio-ai-not-configured")).toBeVisible();
  await expect(page.getByTestId("portfolio-ai-not-configured")).toContainText("AI");
  await expect(page.getByRole("link", { name: /AI Settings|AI 設定/ })).toBeVisible();
});
