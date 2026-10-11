import { expect, test } from "./setup";

import { setE2eAuthenticatedUser, setE2eAuthStatusDown } from "./e2eApiMocks";

test("shows server unreachable when auth status fails, then recovers", async ({ page }) => {
  setE2eAuthStatusDown(page, true);

  await page.goto("http://127.0.0.1:3000/");

  await expect(page.locator(".app-auth-gate-unreachable")).toContainText(
    /Cannot reach the server|無法連線到伺服器/,
  );
  await expect(page.locator("#main-content")).toHaveCount(0);

  setE2eAuthenticatedUser(page, { id: "alice", username: "alice" });
  setE2eAuthStatusDown(page, false);

  await page.getByRole("button", { name: /Retry|重試/ }).click();

  await expect(page.locator("#main-content")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".app-auth-user")).toHaveText("alice");
});

test("recovers on its own after auth status starts succeeding", async ({ page }) => {
  setE2eAuthStatusDown(page, true);

  await page.goto("http://127.0.0.1:3000/");
  await expect(page.locator(".app-auth-gate-unreachable")).toContainText(
    /Cannot reach the server|無法連線到伺服器/,
  );

  setE2eAuthenticatedUser(page, { id: "alice", username: "alice" });
  setE2eAuthStatusDown(page, false);

  await expect(page.locator("#main-content")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".app-auth-user")).toHaveText("alice");
});

test("login page shows unreachable instead of the sign-in form", async ({ page }) => {
  setE2eAuthStatusDown(page, true);

  await page.goto("http://127.0.0.1:3000/login");

  await expect(page.locator(".app-auth-gate-unreachable")).toContainText(
    /Cannot reach the server|無法連線到伺服器/,
  );
  await expect(page.getByRole("heading", { name: "Sign in" })).toHaveCount(0);
});
