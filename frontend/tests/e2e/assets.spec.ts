import { expect, test } from "@playwright/test";


test("assets page can add assets and reflect consolidated totals", async ({ page }) => {
  await page.goto("/assets");
  await expect(page.locator("#main-content")).toBeVisible();

  const uniqueUsdName = `USD Cash ${Date.now()}`;
  await page.locator("#portfolio-asset-name").fill(uniqueUsdName);
  await page.locator("#portfolio-asset-value").fill("1000");
  await page.getByTestId("asset-add-button").click();
  await expect(page.getByText(uniqueUsdName)).toBeVisible();

  await page.locator("#portfolio-asset-currency").selectOption("TWD");
  const uniqueTwdName = `TWD Cash ${Date.now()}`;
  await page.locator("#portfolio-asset-name").fill(uniqueTwdName);
  await page.locator("#portfolio-asset-value").fill("32000");
  await page.getByTestId("asset-add-button").click();
  await expect(page.getByText(uniqueTwdName)).toBeVisible();

  await expect(page.getByTestId("assets-total-usd")).not.toHaveText(/^\$0(?:\.00)?$/);
  await expect(page.getByTestId("assets-total-twd")).not.toHaveText(/^NT\$0(?:\.00)?$/);
  await expect(page.getByTestId("assets-combined-usd")).not.toHaveText(/^\$0(?:\.00)?$/);
  await expect(page.getByTestId("assets-combined-twd")).not.toHaveText(/^NT\$0(?:\.00)?$/);

  await page
    .locator("tr", { hasText: uniqueUsdName })
    .locator("button")
    .first()
    .click();
  await page
    .locator("tr", { hasText: uniqueTwdName })
    .locator("button")
    .first()
    .click();
  await expect(page.getByText(uniqueUsdName)).toHaveCount(0);
  await expect(page.getByText(uniqueTwdName)).toHaveCount(0);
});
