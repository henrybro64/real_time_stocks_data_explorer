import { setE2eAuthenticatedUser } from "./e2eApiMocks";
import { expect, test } from "./setup";

/**
 * #331 / #346 — Profile MACD panel + research chip browser.
 * Mock-backed; does not require live upstream providers.
 */

test("stock profile shows TW MACD/KD panel from stored-only endpoint", async ({ page }) => {
  setE2eAuthenticatedUser(page, { id: "macd-user", username: "macd-user" });

  await page.route("**/api/stocks/TW%3A2330/technical-indicators", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        stockId: "TW:2330",
        market: "TW",
        upstreamFetch: false,
        calculationVersion: "e2e",
        daily: {
          quality: "ok",
          timeframe: "daily",
          asOf: "2024-06-27",
          macdDif: 1.2,
          macdSignal: 0.8,
          macdHistogram: 0.4,
          kdK: 55,
          kdD: 52,
          passStatus: "pass",
        },
        monthly: {
          quality: "ok",
          timeframe: "monthly",
          asOf: "2024-05-31",
          macdDif: 2.1,
          macdSignal: 1.5,
          macdHistogram: 0.6,
          kdK: 60,
          kdD: 58,
          passStatus: "pass",
        },
      }),
    });
  });

  await page.goto("/stocks/TW:2330");
  await expect(page.getByTestId("macd-kd-indicators-panel")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("macd-kd-indicators-panel")).toContainText(/MACD|KD|DIF/i);
});

test("stock profile shows unavailable (stale) for stale mock", async ({ page }) => {
  setE2eAuthenticatedUser(page, { id: "macd-stale", username: "macd-stale" });

  await page.route("**/api/stocks/TW%3A2330/technical-indicators", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        stockId: "TW:2330",
        market: "TW",
        upstreamFetch: false,
        calculationVersion: "e2e",
        daily: {
          quality: "stale",
          timeframe: "daily",
          asOf: "2021-06-28",
          lastBarDate: "2021-06-28",
          unavailableReason: "latest_bar_before_last_completed_session",
          macdDif: null,
          passStatus: null,
        },
        monthly: {
          quality: "stale",
          timeframe: "monthly",
          asOf: "2021-05-31",
          unavailableReason: "latest_bar_before_last_completed_session",
          macdDif: null,
          passStatus: null,
        },
      }),
    });
  });

  await page.goto("/stocks/TW:2330");
  await expect(page.getByTestId("macd-kd-daily-unavailable")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("macd-kd-daily-unavailable")).toContainText(/unavailable \(stale\)|不可用（已過期/i);
});

test("research evidence tab shows 台積電 chip sections when snapshot is pinned", async ({
  page,
}) => {
  setE2eAuthenticatedUser(page, { id: "chip-user", username: "chip-user" });
  await page.goto("/research/tw");
  await expect(page.getByTestId("tw-research-tabs")).toBeVisible();
  await page.getByTestId("tw-research-tab-evidence").click();
  await expect(page.getByTestId("tw-research-chip-evidence")).toBeVisible();
  await expect(page.getByTestId("tw-research-chip-row-TW:2330")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("chip-evidence-institutional")).toContainText(/三大法人|Institutional/i);
  await expect(page.getByTestId("chip-evidence-margin")).toContainText(/融資|Margin/i);
  await expect(page.getByTestId("chip-evidence-tdcc")).toContainText(/大戶動向|Large holders/i);
});

test("research chip browser works on mobile viewport 390×844", async ({ page }) => {
  setE2eAuthenticatedUser(page, { id: "chip-mobile", username: "chip-mobile" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/research/tw");
  await page.getByTestId("tw-research-tab-evidence").click();
  await expect(page.getByTestId("tw-research-chip-evidence")).toBeVisible();
  await expect(page.getByTestId("tw-research-chip-row-TW:2330")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("chip-evidence-institutional")).toBeVisible();
});
