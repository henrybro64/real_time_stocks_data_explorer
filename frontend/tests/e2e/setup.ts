import { test as base, expect } from "@playwright/test";

import { installE2eSearchMock } from "./helpers";

/**
 * Every E2E test gets a deterministic /api/search stub so CI does not depend on
 * live quote-provider search (which can ECONNRESET the backend proxy).
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    await installE2eSearchMock(page);
    await use(page);
  },
});

export { expect };
