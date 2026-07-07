import { expect, test } from "./setup";

import { installE2eApiMocks, setE2eAuthenticatedUser } from "./e2eApiMocks";
import {
  addTwStockFromSearchCard,
  expectPortfolioPosition,
  expectWatchlistCard,
  openHome,
  TW_STOCK_ID,
} from "./helpers";

test("watchlist, portfolio, and alerts stay isolated across user sessions", async ({ browser, page }) => {
  const bobContext = await browser.newContext();
  const bobPage = await bobContext.newPage();

  await installE2eApiMocks(bobPage);
  setE2eAuthenticatedUser(page, { id: "alice", username: "alice" });
  setE2eAuthenticatedUser(bobPage, { id: "bob", username: "bob" });

  try {
    await openHome(page);
    await expect(page.locator(".app-auth-user")).toHaveText("alice");
    await addTwStockFromSearchCard(page, "stock-search-section");
    await expectWatchlistCard(page);

    await bobPage.goto("http://127.0.0.1:3000/");
    await expect(bobPage.locator("#main-content")).toBeVisible();
    await expect(bobPage.locator(".app-auth-user")).toHaveText("bob");
    await expect(
      bobPage.getByTestId("watchlist-section").getByTestId(`stock-card-${TW_STOCK_ID}`),
    ).toHaveCount(0);

    await page.goto("/portfolio");
    await expect(page.locator("#main-content")).toBeVisible();
    await addTwStockFromSearchCard(page, "portfolio-search-section");
    await expectPortfolioPosition(page);

    await bobPage.goto("http://127.0.0.1:3000/portfolio");
    await expect(bobPage.locator("#main-content")).toBeVisible();
    await expect(bobPage.getByTestId(`portfolio-position-${TW_STOCK_ID}`)).toHaveCount(0);

    await page.goto("/alerts?stockId=TW%3A2330");
    await expect(page.locator("#main-content")).toBeVisible();
    await page.getByLabel("門檻價格").fill("940");
    await page.getByTestId("alert-submit-button").click();
    await expect(page.getByTestId("alerts-rule-row-1")).toBeVisible();
    await expect(page.getByTestId("alerts-event-row-1")).toBeVisible();

    await bobPage.goto("http://127.0.0.1:3000/alerts?stockId=TW%3A2330");
    await expect(bobPage.locator("#main-content")).toBeVisible();
    await expect(bobPage.locator('[data-testid^="alerts-rule-row-"]')).toHaveCount(0);
    await expect(bobPage.locator('[data-testid^="alerts-event-row-"]')).toHaveCount(0);
  } finally {
    await bobContext.close();
  }
});
