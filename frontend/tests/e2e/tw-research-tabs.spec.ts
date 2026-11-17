import { setE2eAuthenticatedUser } from "./e2eApiMocks";
import { openTwResearchProgressDisclosure } from "./helpers";
import { expect, test } from "./setup";

/**
 * #339 / #361 — single-flow research center (former three-tab layout).
 * Mock-backed; does not require a live backend or CronJob.
 */

async function openResearchPage(page: import("@playwright/test").Page) {
  setE2eAuthenticatedUser(page, { id: "tabs-user", username: "tabs-user" });
  await page.goto("/research/tw");
  await expect(page.locator("#main-content")).toBeVisible();
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
  await expect(page.getByTestId("tw-research-single-flow")).toBeVisible();
}

test("single-flow stages expose fundamentals, watchlist, and progress disclosure", async ({
  page,
}) => {
  await openResearchPage(page);

  await expect(page.getByTestId("tw-research-phase-banner")).toBeVisible();
  await expect(page.getByTestId("tw-research-phase-label")).toContainText(/Step 4|步驟 4/);
  await expect(page.getByTestId("tw-research-investor-summary")).toBeVisible();
  await expect(page.getByTestId("tw-research-phase-banner")).not.toContainText(/Notion|CronJob|#\d{3}/);

  await expect(page.getByTestId("tw-research-flow-stage-1")).toBeVisible();
  await expect(page.getByTestId("tw-research-fundamentals")).toBeVisible();

  await expect(page.getByTestId("tw-research-watchlist")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("tw-research-tab-watchlist")).toBeVisible();

  await openTwResearchProgressDisclosure(page);
  await expect(page.getByTestId("tw-research-latest-verified")).toBeVisible();

  await expect(page.getByTestId("tw-research-chip-evidence")).toBeVisible({
    timeout: 15_000,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
  await expect(page.getByTestId("tw-research-phase-banner")).toBeVisible();
});

test("analyst evidence example loads as labeled fixture only", async ({ page }) => {
  await openResearchPage(page);
  const exampleHelp = page.getByTestId("tw-research-example-help");
  await exampleHelp.locator("xpath=ancestor::details").locator("summary").click();
  await exampleHelp.getByTestId("tw-research-load-evidence-example").click();
  await expect(page.getByTestId("tw-research-example-evidence")).toContainText("exampleOnly");
  await expect(page.getByTestId("tw-research-example-evidence")).toContainText(
    "不是即時投資建議",
  );
});

test("watchlist remains available after reload and does not invent trading picks", async ({
  page,
}) => {
  await openResearchPage(page);
  const watchlist = page.getByTestId("tw-research-watchlist");
  await expect(watchlist).toBeVisible({ timeout: 15_000 });
  await expect(watchlist).toContainText(
    /not a trading recommendation|不是交易建議|觀察/,
  );
  await expect(watchlist).not.toContainText(/Notion|#\d{3}/);
  await page.reload();
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
  await expect(page.getByTestId("tw-research-watchlist")).toBeVisible({
    timeout: 15_000,
  });
});
