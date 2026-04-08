import { expect, test } from "@playwright/test";

import { openHome, searchFromHome } from "./helpers";


test("user can search from the home page and see a Taiwan stock result", async ({ page }) => {
  await openHome(page);
  const card = await searchFromHome(page);

  await expect(card).toContainText("2330");
  await expect(card.getByTestId("stock-card-view-TW:2330")).toBeVisible();
});
