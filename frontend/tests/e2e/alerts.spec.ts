import { expect, test } from "./setup";


test("alerts page creates a threshold rule and shows the triggered event history", async ({ page }) => {
  await page.goto("/alerts?stockId=TW%3A2330");
  await expect(page.locator("#main-content")).toBeVisible();

  await expect(page.getByLabel("股票 ID")).toHaveValue("TW:2330");
  await page.getByLabel("門檻價格").fill("940");
  await page.getByTestId("alert-submit-button").click();

  await expect(page.getByTestId("alerts-rule-row-1")).toBeVisible();
  await expect(page.getByTestId("alerts-event-row-1")).toBeVisible();
  await expect(page.getByText("2330 rose above 940.00 (observed 950.00).")).toBeVisible();
});
