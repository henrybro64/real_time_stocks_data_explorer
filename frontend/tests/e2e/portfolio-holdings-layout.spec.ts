import type { Page } from "@playwright/test";

import { expect, test } from "./setup";

import {
  addTwStockFromSearchCard,
  ensurePortfolioDoesNotContain,
  expectPortfolioPosition,
  TW_STOCK_ID,
} from "./helpers";

test("portfolio holdings stay inside the tile when the table is wider than the card", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await ensurePortfolioDoesNotContain(page, TW_STOCK_ID);
  await addTwStockFromSearchCard(page, "portfolio-search-section");
  await expectPortfolioPosition(page);

  const section = page.getByTestId("portfolio-market-section-tw");
  await expect(section).toBeVisible();
  await expect(page.getByRole("heading", { name: "台灣股票" })).toBeVisible();
  await section.locator("table").scrollIntoViewIfNeeded();

  const desktop = await measureHoldingsLayout(page);
  expectHoldingsStayInsideTile(desktop);
  expect(desktop.pageScrollWidth).toBeLessThanOrEqual(desktop.viewportWidth + 1);

  await page.setViewportSize({ width: 390, height: 800 });
  await section.locator("table").scrollIntoViewIfNeeded();
  expectHoldingsStayInsideTile(await measureHoldingsLayout(page));

  await page.getByTestId(`portfolio-remove-${TW_STOCK_ID}`).click();
  await expect(page.getByTestId(`portfolio-position-${TW_STOCK_ID}`)).toHaveCount(0);
});

function expectHoldingsStayInsideTile(layout: Awaited<ReturnType<typeof measureHoldingsLayout>>) {
  expect(layout.sampledOutsideTile).toBe(true);
  expect(layout.pageScrollWidth).toBeLessThan(layout.tableScrollWidth);
  expect(Math.abs(layout.tileWidth - layout.summaryWidth)).toBeLessThan(2);
  expect(layout.scrollportInsideTile).toBe(true);
  expect(layout.tableScrollsInsideCard).toBe(true);
  expect(layout.paintsPastTile).toBe(false);
}

async function measureHoldingsLayout(page: Page) {
  return page.evaluate(() => {
    const tile = document.querySelector(".app-portfolio-holdings-tile");
    const summary = document
      .querySelector("[data-testid='portfolio-total-twd']")
      ?.closest(".app-section-tile");
    const section = document.querySelector("[data-testid='portfolio-market-section-tw']");
    const table = section?.querySelector("table");
    const scrollport = section?.querySelector(".cds--data-table-content");
    if (
      !(tile instanceof HTMLElement) ||
      !(summary instanceof HTMLElement) ||
      !(table instanceof HTMLElement) ||
      !(scrollport instanceof HTMLElement)
    ) {
      throw new Error("Portfolio holdings layout nodes were not rendered.");
    }

    const tileBox = tile.getBoundingClientRect();
    const summaryBox = summary.getBoundingClientRect();
    const scrollBox = scrollport.getBoundingClientRect();
    const tableBox = table.getBoundingClientRect();
    const probeY = Math.min(Math.max(tableBox.top + 8, 0), window.innerHeight - 1);
    const probeX = Math.min(tileBox.right + 8, window.innerWidth - 1);
    const pastTile = probeX > tileBox.right + 1;
    const hit = pastTile ? document.elementFromPoint(probeX, probeY) : null;

    return {
      viewportWidth: window.innerWidth,
      pageScrollWidth: document.documentElement.scrollWidth,
      tableScrollWidth: scrollport.scrollWidth,
      tileWidth: tileBox.width,
      summaryWidth: summaryBox.width,
      sampledOutsideTile: pastTile,
      scrollportInsideTile:
        scrollBox.left >= tileBox.left - 1 && scrollBox.right <= tileBox.right + 1,
      tableScrollsInsideCard: scrollport.scrollWidth > scrollport.clientWidth + 1,
      paintsPastTile: Boolean(hit?.closest("table")),
    };
  });
}
