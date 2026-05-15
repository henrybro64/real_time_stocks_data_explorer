import { expect, test } from "./setup";

import {
  addTwStockFromSearchCard,
  ensurePortfolioDoesNotContain,
  expectPortfolioPosition,
  TW_STOCK_ID,
} from "./helpers";


test("portfolio add, edit quantity, verify totals, and remove holding", async ({ page }) => {
  await ensurePortfolioDoesNotContain(page, TW_STOCK_ID);

  await addTwStockFromSearchCard(page, "portfolio-search-section");
  await expectPortfolioPosition(page);
  const row = page.getByTestId(`portfolio-position-${TW_STOCK_ID}`);

  const quantityInput = page.getByTestId(`portfolio-quantity-input-${TW_STOCK_ID}`);
  await quantityInput.fill("3");
  await quantityInput.press("Enter");
  await expect(quantityInput).toHaveValue("3");

  const averageCostInput = page.getByTestId(`portfolio-average-cost-input-${TW_STOCK_ID}`);
  await averageCostInput.fill("900");
  await averageCostInput.press("Enter");
  await expect(averageCostInput).toHaveValue("900");

  await expect(page.getByTestId("portfolio-total-twd")).not.toHaveText(/^NT\$0(?:\.00)?$/);

  await row.getByTestId(`portfolio-remove-${TW_STOCK_ID}`).click();
  await expect(row).toHaveCount(0);
});
