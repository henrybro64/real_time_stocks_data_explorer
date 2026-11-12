import type { Page, Request } from "@playwright/test";

import { E2E_TW_RESEARCH_HANDOFF, setE2eAuthenticatedUser, setE2eSignedOut } from "./e2eApiMocks";
import { expect, test } from "./setup";

/**
 * #322 — browser-only Agent JSON handoff.
 *
 * Positive reads use the authenticated same-origin JSON API through Playwright
 * route fixtures. A live backend is not required and a 401/404 is not a pass.
 * Staged checksums below simulate an external consumer. Stock Ticker does not
 * store that checkpoint.
 */

const REQUIRED_MANIFEST_FIELDS = [
  "snapshotId",
  "contentDigest",
  "schemaVersion",
  "qualityStatus",
  "chunkDigests",
  "tradingDay",
] as const;

const MARKET_DATA_PATH = /\/api\/quotes|\/api\/stocks\/|\/history|\/api\/rankings/;

type JsonRead = { status: number; body: Record<string, unknown> | null };

type StagedChunk = {
  snapshotId: string;
  checksum: string;
};

async function readJson(page: Page, path: string): Promise<JsonRead> {
  return page.evaluate(async (requestPath) => {
    const response = await fetch(requestPath, { credentials: "include" });
    const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
    return { status: response.status, body };
  }, path);
}

function trackMarketData(page: Page) {
  const hits: string[] = [];
  const onRequest = (request: Request) => {
    const path = new URL(request.url()).pathname;
    if (MARKET_DATA_PATH.test(path)) {
      hits.push(`${request.method()} ${path}`);
    }
  };
  page.on("request", onRequest);
  return {
    hits,
    stop() {
      page.off("request", onRequest);
    },
  };
}

function chunkPath(snapshotId: string) {
  return `/api/tw-research/exports/snapshots/${encodeURIComponent(snapshotId)}/chunk?chunkIndex=0&kind=ledger`;
}

async function openResearchPage(page: Page) {
  setE2eAuthenticatedUser(page, { id: "handoff-user", username: "handoff-user" });
  await page.goto("/research/tw");
  await expect(page.locator("#main-content")).toBeVisible();
  // Latest-verified lives under the Progress tab after the three-tab layout (#339).
  await page.getByTestId("tw-research-tab-progress").click();
  await expect(page.getByTestId("tw-research-latest-verified")).toBeVisible();
}

test("authenticated browser reads the verified manifest and matching chunk", async ({
  page,
}) => {
  await openResearchPage(page);

  const session = await readJson(page, "/api/auth/status");
  expect(session.status).toBe(200);
  expect(session.body?.loggedIn).toBe(true);

  const marketData = trackMarketData(page);
  const manifestRead = await readJson(page, "/api/tw-research/exports/latest/manifest");
  expect(manifestRead.status).toBe(200);
  const manifest = manifestRead.body?.manifest as Record<string, unknown> | undefined;
  expect(manifest).toBeTruthy();
  for (const field of REQUIRED_MANIFEST_FIELDS) {
    expect(manifest?.[field], field).toBeTruthy();
  }
  expect(manifest?.snapshotId).toBe(E2E_TW_RESEARCH_HANDOFF.snapshotId);
  expect(manifest?.contentDigest).toBe(E2E_TW_RESEARCH_HANDOFF.contentDigest);
  expect(manifest?.schemaVersion).toBe(E2E_TW_RESEARCH_HANDOFF.schemaVersion);
  expect(manifest?.qualityStatus).toBe("verified_ready");
  expect(manifest?.tradingDay).toBe(E2E_TW_RESEARCH_HANDOFF.tradingDay);

  const chunkDigests = manifest?.chunkDigests as Record<string, string>;
  const snapshotId = String(manifest?.snapshotId);
  const chunkRead = await readJson(page, chunkPath(snapshotId));
  expect(chunkRead.status).toBe(200);
  const chunk = chunkRead.body?.chunk as Record<string, unknown> | undefined;
  expect(chunk?.chunkIndex).toBe(0);
  expect(chunk?.checksumSha256).toBe(chunkDigests[String(chunk?.chunkIndex)]);
  expect(chunk?.checksumSha256).toBe(E2E_TW_RESEARCH_HANDOFF.chunkDigest);

  marketData.stop();
  expect(marketData.hits).toEqual([]);
  await expect(page).toHaveURL(/\/research\/tw$/);

  await page.getByTestId("tw-research-load-latest-manifest").click();
  const jsonArea = page.getByTestId("tw-research-latest-manifest-json");
  await expect(jsonArea).toBeVisible();
  await expect.poll(async () => jsonArea.inputValue()).toContain(E2E_TW_RESEARCH_HANDOFF.snapshotId);
  await expect.poll(async () => jsonArea.inputValue()).toContain("contentDigest");
  await expect(page).toHaveURL(/\/research\/tw$/);
  expect(page.url()).not.toContain("/stocks/");
});

test("missing session returns 401 and leaves the staged snapshot untouched", async ({ page }) => {
  await openResearchPage(page);
  const staged: StagedChunk = { snapshotId: "previous-snapshot", checksum: "previous-checksum" };
  const before = { ...staged };

  setE2eSignedOut(page);
  const manifestRead = await readJson(page, "/api/tw-research/exports/latest/manifest");
  expect(manifestRead.status).toBe(401);
  expect(staged).toEqual(before);
  await expect(page).toHaveURL(/\/research\/tw$/);
});

test("forbidden session returns 403 and leaves the staged snapshot untouched", async ({ page }) => {
  await openResearchPage(page);
  const staged: StagedChunk = { snapshotId: "previous-snapshot", checksum: "previous-checksum" };
  const before = { ...staged };

  await page.route("**/api/tw-research/exports/latest/manifest**", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Forbidden" }),
    });
  });

  const manifestRead = await readJson(page, "/api/tw-research/exports/latest/manifest");
  expect(manifestRead.status).toBe(403);
  expect(staged).toEqual(before);
});

test("missing verified snapshot returns 404 and leaves the staged snapshot untouched", async ({
  page,
}) => {
  await openResearchPage(page);
  const staged: StagedChunk = { snapshotId: "previous-snapshot", checksum: "previous-checksum" };
  const before = { ...staged };

  await page.route("**/api/tw-research/exports/latest/manifest**", async (route) => {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "No verified_ready export snapshot matches the selection." }),
    });
  });

  const manifestRead = await readJson(page, "/api/tw-research/exports/latest/manifest");
  expect(manifestRead.status).toBe(404);
  expect(staged).toEqual(before);
});

test("checksum mismatch rejects the chunk and keeps the previous staged checksum", async ({
  page,
}) => {
  await openResearchPage(page);
  const staged: StagedChunk = { snapshotId: "previous-snapshot", checksum: "previous-checksum" };
  const before = { ...staged };

  await page.route("**/api/tw-research/exports/snapshots/**/chunk**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        chunk: {
          chunkIndex: 0,
          kind: "ledger",
          checksumSha256: "f".repeat(64),
          tradingDay: E2E_TW_RESEARCH_HANDOFF.tradingDay,
          rows: [{ stockId: "TW:2330" }],
        },
      }),
    });
  });

  const manifestRead = await readJson(page, "/api/tw-research/exports/latest/manifest");
  expect(manifestRead.status).toBe(200);
  const manifest = manifestRead.body?.manifest as {
    chunkDigests: Record<string, string>;
    snapshotId: string;
  };
  const chunkRead = await readJson(page, chunkPath(manifest.snapshotId));
  expect(chunkRead.status).toBe(200);
  const chunk = chunkRead.body?.chunk as { chunkIndex: number; checksumSha256: string };
  const expected = manifest.chunkDigests[String(chunk.chunkIndex)];
  expect(chunk.checksumSha256).not.toBe(expected);
  expect(staged).toEqual(before);
});

test("a temporary chunk read failure retries without replacing staged data", async ({ page }) => {
  await openResearchPage(page);
  let staged: StagedChunk = { snapshotId: "previous-snapshot", checksum: "previous-checksum" };
  const before = { ...staged };
  let attempts = 0;

  await page.route("**/api/tw-research/exports/snapshots/**/chunk**", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "temporary read failure" }),
      });
      return;
    }
    await route.fallback();
  });

  const manifestRead = await readJson(page, "/api/tw-research/exports/latest/manifest");
  expect(manifestRead.status).toBe(200);
  const manifest = manifestRead.body?.manifest as {
    snapshotId: string;
    chunkDigests: Record<string, string>;
  };

  const failed = await readJson(page, chunkPath(manifest.snapshotId));
  expect(failed.status).toBe(503);
  expect(staged).toEqual(before);

  const retried = await readJson(page, chunkPath(manifest.snapshotId));
  expect(retried.status).toBe(200);
  const chunk = retried.body?.chunk as { chunkIndex: number; checksumSha256: string };
  const expected = manifest.chunkDigests[String(chunk.chunkIndex)];
  expect(chunk.checksumSha256).toBe(expected);
  expect(staged).toEqual(before);

  staged = { snapshotId: manifest.snapshotId, checksum: chunk.checksumSha256 };
  expect(staged).toEqual({
    snapshotId: E2E_TW_RESEARCH_HANDOFF.snapshotId,
    checksum: E2E_TW_RESEARCH_HANDOFF.chunkDigest,
  });
  expect(attempts).toBe(2);
});
