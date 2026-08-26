import { expect, test } from "./setup";


test("rankings page shows snapshot metadata and TW daily warning", async ({ page }) => {
  await page.goto("/rankings");

  await expect(page.locator("#main-content")).toBeVisible();
  await expect(page.getByTestId("rankings-row-TW:2330")).toBeVisible();
  await expect(page.getByTestId("rankings-source-type-tag")).toContainText("免費公開");
  await expect(page.getByTestId("rankings-snapshot-state-tag")).toContainText("台股法人日資料");
  await expect(page.getByTestId("rankings-snapshot-timestamp")).toContainText("快照時間");
  await expect(page.getByTestId("rankings-warning-message")).toContainText(
    "台股三大法人買賣超為日資料",
  );
});


test("user can switch Current -> historical date -> Current on rankings", async ({ page }) => {
  await page.goto("/rankings");

  await expect(page.locator("#main-content")).toBeVisible();
  await expect(page.getByTestId("rankings-row-TW:2330")).toBeVisible();
  await expect(page.getByTestId("rankings-date-select")).toBeVisible();

  await page.getByTestId("rankings-date-select").selectOption("2026-08-20");
  await expect(page.getByTestId("rankings-historical-banner")).toContainText("2026-08-20");
  await expect(page.getByTestId("rankings-refresh-source")).toHaveCount(0);

  await page.getByTestId("rankings-date-select").selectOption("current");
  await expect(page.getByTestId("rankings-historical-banner")).toHaveCount(0);
  await expect(page.getByTestId("rankings-refresh-source")).toBeVisible();
});
