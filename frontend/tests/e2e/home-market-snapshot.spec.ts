import { expect, test } from "./setup";

import { openHome } from "./helpers";

test("home market snapshot shows USD/TWD and WTI cards near the top", async ({ page }) => {
  await openHome(page);

  const snapshot = page.getByTestId("market-snapshot-section");
  await expect(snapshot).toBeVisible();
  await expect(page.getByTestId("market-snapshot-fx-value")).toBeVisible();
  await expect(page.getByTestId("market-snapshot-wti-value")).toBeVisible();
  await expect(page.getByTestId("market-snapshot-fx-value")).toContainText("32");
  await expect(page.getByTestId("market-snapshot-wti-value")).toContainText("68.45");

  const heroBox = await page.locator(".app-hero-tile").boundingBox();
  const snapshotBox = await snapshot.boundingBox();
  expect(heroBox).not.toBeNull();
  expect(snapshotBox).not.toBeNull();
  expect(snapshotBox!.y).toBeGreaterThan(heroBox!.y);
});

test("market snapshot stacks cards on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openHome(page);

  const fxCard = page.getByTestId("market-snapshot-fx");
  const wtiCard = page.getByTestId("market-snapshot-wti");
  const fxBox = await fxCard.boundingBox();
  const wtiBox = await wtiCard.boundingBox();

  expect(fxBox).not.toBeNull();
  expect(wtiBox).not.toBeNull();
  expect(Math.abs(fxBox!.x - wtiBox!.x)).toBeLessThan(8);
  expect(wtiBox!.y).toBeGreaterThan(fxBox!.y);
});

test("market snapshot shows two-column layout on desktop", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openHome(page);

  const fxCard = page.getByTestId("market-snapshot-fx");
  const wtiCard = page.getByTestId("market-snapshot-wti");
  const fxBox = await fxCard.boundingBox();
  const wtiBox = await wtiCard.boundingBox();

  expect(fxBox).not.toBeNull();
  expect(wtiBox).not.toBeNull();
  expect(Math.abs(fxBox!.x - wtiBox!.x)).toBeGreaterThan(16);
  expect(Math.abs(fxBox!.y - wtiBox!.y)).toBeLessThan(8);
});
