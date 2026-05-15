import { test as base, expect } from "@playwright/test";

import { installE2eApiMocks } from "./e2eApiMocks";

/**
 * Stub search, quotes, watchlist, and portfolio APIs so E2E tests validate UI wiring
 * without live market providers (which cause ECONNRESET / missing DOM nodes in CI).
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    await installE2eApiMocks(page);
    await use(page);
  },
});

export { expect };
