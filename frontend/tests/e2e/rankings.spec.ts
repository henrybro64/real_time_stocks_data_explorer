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


test("user can switch to U.S. rankings and see explicit unsupported institutional state", async ({
  page,
}) => {
  await page.goto("/rankings");

  await expect(page.locator("#main-content")).toBeVisible();
  await page.getByLabel("市場").selectOption("US");
  await page.getByTestId("rankings-tab-net_buy").click();

  await expect(page.getByTestId("rankings-unsupported-message")).toContainText(
    "美股法人買超／賣超",
  );
  await expect(page.getByTestId("rankings-source-type-tag")).toContainText("不支援");
  await expect(page.getByTestId("rankings-snapshot-state-tag")).toContainText("不支援");
});
