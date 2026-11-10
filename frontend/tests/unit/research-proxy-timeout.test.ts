import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { RESEARCH_PROXY_TIMEOUT_MS } from "../../research-proxy-timeout.mjs";

describe("research rewrite proxy timeout", () => {
  it("outlasts the default 120s batch without raising the 600s route cap", () => {
    expect(RESEARCH_PROXY_TIMEOUT_MS).toBe(150_000);
    expect(RESEARCH_PROXY_TIMEOUT_MS).toBeGreaterThan(120_000);
    expect(RESEARCH_PROXY_TIMEOUT_MS).toBeLessThan(600_000);
    const config = readFileSync(join(process.cwd(), "next.config.mjs"), "utf8");
    expect(config).toContain("proxyTimeout: RESEARCH_PROXY_TIMEOUT_MS");
  });
});
