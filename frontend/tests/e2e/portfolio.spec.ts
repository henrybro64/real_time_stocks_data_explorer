import { expect, test } from "@playwright/test";

import {
  ensurePortfolioDoesNotContain,
  searchFromPortfolio,
  TW_STOCK_ID,
} from "./helpers";


test("portfolio add, edit quantity, verify totals, and remove holding", async ({ page }) => {
  await ensurePortfolioDoesNotContain(page, TW_STOCK_ID);

  const stockCard = await searchFromPortfolio(page);
  await stockCard.getByTestId(`stock-card-toggle-${TW_STOCK_ID}`).click();

  const row = page.getByTestId(`portfolio-position-${TW_STOCK_ID}`);
  await expect(row).toBeVisible();

  const quantityInput = page.getByTestId(`portfolio-quantity-input-${TW_STOCK_ID}`);
  await quantityInput.fill("3");
  await quantityInput.press("Enter");
  await expect(quantityInput).toHaveValue("3");
  await expect(page.getByTestId("portfolio-total-twd")).not.toHaveText(/^NT\$0(?:\.00)?$/);

  await row.getByTestId(`portfolio-remove-${TW_STOCK_ID}`).click();
  await expect(row).toHaveCount(0);
});
