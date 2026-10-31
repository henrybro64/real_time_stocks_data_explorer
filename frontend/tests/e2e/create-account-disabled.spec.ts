import { expect, test } from "./setup";

import { setE2eSignedOut } from "./e2eApiMocks";

test("login page keeps Create account visible and disabled (#304)", async ({ page }) => {
  const registerCalls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/auth/register/local")) {
      registerCalls.push(request.url());
    }
  });
  setE2eSignedOut(page);

  await page.goto("/login");

  const create = page.getByRole("button", { name: "Create account" });
  await expect(create).toBeVisible();
  await expect(create).toBeDisabled();
  await expect(page.getByText("Sign in with your account.")).toBeVisible();
  await expect(page.getByText("Create one in one click")).toHaveCount(0);

  await page.keyboard.press("Enter");
  await page.keyboard.press(" ");
  await expect(page).toHaveURL(/\/login$/);
  expect(registerCalls).toEqual([]);

  await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled();
  await expect(page.getByRole("link", { name: "Sign in with Microsoft" })).toHaveAttribute(
    "href",
    "/api/auth/login/entra",
  );
});
