import { expect, test } from "./setup";

import { installE2eApiMocks, setE2eAuthenticatedUser } from "./e2eApiMocks";
import {
  addTwStockFromSearchCard,
  expectPortfolioPosition,
  expectWatchlistCard,
  openHome,
  TW_STOCK_ID,
} from "./helpers";

async function fetchJson<T>(page: import("@playwright/test").Page, path: string): Promise<T> {
  return page.evaluate(async (requestPath) => {
    const response = await fetch(requestPath);
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${requestPath}`);
    }
    return response.json() as Promise<T>;
  }, path);
}

async function fetchStatus(
  page: import("@playwright/test").Page,
  path: string,
  init: RequestInit,
): Promise<number> {
  return page.evaluate(
    async ({ requestPath, requestInit }) => {
      const response = await fetch(requestPath, requestInit);
      return response.status;
    },
    { requestPath: path, requestInit: init },
  );
}

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

    const bobWatchlistDeleteStatus = await fetchStatus(
      bobPage,
      `/api/watchlist/items/${encodeURIComponent(TW_STOCK_ID)}`,
      { method: "DELETE" },
    );
    expect(bobWatchlistDeleteStatus).toBe(200);
    await expectWatchlistCard(page);

    await page.goto("/portfolio");
    await expect(page.locator("#main-content")).toBeVisible();
    await addTwStockFromSearchCard(page, "portfolio-search-section");
    await expectPortfolioPosition(page);

    const alicePositions = await fetchJson<Array<{ id: number; stockId: string }>>(
      page,
      "/api/portfolio/positions",
    );
    const alicePositionId = alicePositions[0]?.id;
    expect(alicePositionId).toBeTruthy();

    await bobPage.goto("http://127.0.0.1:3000/portfolio");
    await expect(bobPage.locator("#main-content")).toBeVisible();
    await expect(bobPage.getByTestId(`portfolio-position-${TW_STOCK_ID}`)).toHaveCount(0);

    const bobDeletePositionStatus = await fetchStatus(
      bobPage,
      `/api/portfolio/positions/${alicePositionId}`,
      { method: "DELETE" },
    );
    expect(bobDeletePositionStatus).toBe(404);
    await expectPortfolioPosition(page);

    await page.goto("/alerts?stockId=TW%3A2330");
    await expect(page.locator("#main-content")).toBeVisible();
    await page.getByLabel("門檻價格").fill("940");
    await page.getByTestId("alert-submit-button").click();
    await expect(page.getByTestId("alerts-rule-row-1")).toBeVisible();
    await expect(page.getByTestId("alerts-event-row-1")).toBeVisible();

    const aliceRules = await fetchJson<Array<{ id: number }>>(page, "/api/alerts/rules");
    const aliceRuleId = aliceRules[0]?.id;
    expect(aliceRuleId).toBeTruthy();

    await bobPage.goto("http://127.0.0.1:3000/alerts?stockId=TW%3A2330");
    await expect(bobPage.locator("#main-content")).toBeVisible();
    await expect(bobPage.locator('[data-testid^="alerts-rule-row-"]')).toHaveCount(0);
    await expect(bobPage.locator('[data-testid^="alerts-event-row-"]')).toHaveCount(0);

    const bobPatchRuleStatus = await fetchStatus(bobPage, `/api/alerts/rules/${aliceRuleId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: false }),
    });
    const bobDeleteRuleStatus = await fetchStatus(bobPage, `/api/alerts/rules/${aliceRuleId}`, {
      method: "DELETE",
    });
    expect(bobPatchRuleStatus).toBe(404);
    expect(bobDeleteRuleStatus).toBe(404);
    await expect(page.getByTestId("alerts-rule-row-1")).toBeVisible();
    await expect(page.getByTestId("alerts-event-row-1")).toBeVisible();
  } finally {
    await bobContext.close();
  }
});
