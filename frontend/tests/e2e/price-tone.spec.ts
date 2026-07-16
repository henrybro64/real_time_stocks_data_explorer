import { expect, test } from "./setup";

import { installE2eApiMocks } from "./e2eApiMocks";
import { TPEX_STOCK_ID, TW_STOCK_ID, US_STOCK_ID } from "./helpers";

test("Taiwan and U.S. stock detail pages use market-specific price color conventions", async ({
  page,
}) => {
  await installE2eApiMocks(page);

  await page.goto(`/stock/${encodeURIComponent(TW_STOCK_ID)}`);
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  const twChange = page.getByTestId("quote-summary-change");
  await expect(twChange).toBeVisible();
  await expect(twChange).toHaveClass(/app-price-up/);
  await expect(twChange).toHaveClass(/app-price-convention-taiwan/);
  await expect(twChange).toContainText("+");
  await expect(twChange).not.toHaveClass("app-positive");
  await expect(twChange).not.toHaveClass("app-negative");
  const twChart = page.getByTestId("price-chart");
  await expect(twChart).toHaveAttribute("data-price-convention", "taiwan");
  await expect(twChart.locator(".app-chart-candle-body-up")).toHaveCount(1);

  await page.goto(`/stock/${encodeURIComponent(TPEX_STOCK_ID)}`);
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  const tpexChange = page.getByTestId("quote-summary-change");
  await expect(tpexChange).toHaveClass(/app-price-down/);
  await expect(tpexChange).toHaveClass(/app-price-convention-taiwan/);
  await expect(tpexChange).toContainText("-");
  await expect(page.getByTestId("price-chart")).toHaveAttribute("data-price-convention", "taiwan");

  await page.goto(`/stock/${encodeURIComponent(US_STOCK_ID)}`);
  await expect(page.getByTestId("quote-summary-section")).toBeVisible();
  const usChange = page.getByTestId("quote-summary-change");
  await expect(usChange).toBeVisible();
  await expect(usChange).toHaveClass(/app-price-up/);
  await expect(usChange).toHaveClass(/app-price-convention-us/);
  await expect(usChange).toContainText("+");
  await expect(usChange).not.toHaveClass("app-positive");
  await expect(usChange).not.toHaveClass("app-negative");
  const usChart = page.getByTestId("price-chart");
  await expect(usChart).toHaveAttribute("data-price-convention", "us");
  await expect(usChart.locator(".app-chart-candle-body-up")).toHaveCount(2);
});
