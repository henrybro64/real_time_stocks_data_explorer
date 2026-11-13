import { setE2eAuthenticatedUser } from "./e2eApiMocks";
import { expect, test } from "./setup";

/**
 * #339 — same-page three-tab research center.
 * Mock-backed; does not require a live backend or CronJob.
 */

async function openResearchPage(page: import("@playwright/test").Page) {
  setE2eAuthenticatedUser(page, { id: "tabs-user", username: "tabs-user" });
  await page.goto("/research/tw");
  await expect(page.locator("#main-content")).toBeVisible();
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
}

test("three tabs switch without inventing automatic trading publication", async ({ page }) => {
  await openResearchPage(page);

  await expect(page.getByTestId("tw-research-phase-banner")).toBeVisible();
  await expect(page.getByTestId("tw-research-phase-label")).toContainText(/Step 4|步驟 4/);
  await expect(page.getByTestId("tw-research-investor-summary")).toBeVisible();
  await expect(page.getByTestId("tw-research-phase-banner")).not.toContainText(/Notion|CronJob|#\d{3}/);

  await page.getByTestId("tw-research-tab-watchlist").click();
  await expect(page.getByTestId("tw-research-watchlist")).toBeVisible();

  await page.getByTestId("tw-research-tab-progress").click();
  await expect(page.getByTestId("tw-research-latest-verified")).toBeVisible();

  await page.getByTestId("tw-research-tab-evidence").click();
  await expect(page.getByTestId("tw-research-fundamentals")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
  await expect(page.getByTestId("tw-research-phase-banner")).toBeVisible();
});

test("analyst evidence example loads as labeled fixture only", async ({ page }) => {
  await openResearchPage(page);
  await page
    .getByRole("button", { name: /Load Traditional Chinese example|載入繁中範例/ })
    .click();
  await page.getByTestId("tw-research-advanced-details").locator("summary").first().click();
  await expect(page.getByTestId("tw-research-report-evidence")).toContainText("exampleOnly");
  await expect(page.getByTestId("tw-research-report-evidence")).toContainText("不是即時投資建議");
});

test("watchlist tab remains available after reload and does not invent trading picks", async ({
  page,
}) => {
  await openResearchPage(page);
  await page.getByTestId("tw-research-tab-watchlist").click();
  await expect(page.getByTestId("tw-research-watchlist")).toBeVisible();
  await expect(page.getByTestId("tw-research-watchlist")).toContainText(
    /not a trading recommendation|不是交易建議|觀察/,
  );
  await expect(page.getByTestId("tw-research-watchlist")).not.toContainText(/Notion|#\d{3}/);
  await page.reload();
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
  await page.getByTestId("tw-research-tab-watchlist").click();
  await expect(page.getByTestId("tw-research-watchlist")).toBeVisible();
});
