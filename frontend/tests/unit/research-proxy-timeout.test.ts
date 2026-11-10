import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { RESEARCH_PROXY_TIMEOUT_MS } from "../../research-proxy-timeout.mjs";

describe("research rewrite proxy timeout", () => {
  it("outlasts the default 120s batch without raising the 600s route cap", () => {
    expect(RESEARCH_PROXY_TIMEOUT_MS).toBe(150_000);
    expect(RESEARCH_PROXY_TIMEOUT_MS).toBeGreaterThan(120_000);
    expect(RESEARCH_PROXY_TIMEOUT_MS).toBeLessThan(600_000);
  });

  it("is configured as Next's single global rewrite proxyTimeout", () => {
    const config = readFileSync(join(process.cwd(), "next.config.mjs"), "utf8");
    expect(config).toContain("proxyTimeout: RESEARCH_PROXY_TIMEOUT_MS");
    // Next has no path-scoped proxy timeout; this setting is intentionally global.
    expect(config).not.toMatch(/proxyTimeouts|routeTimeout|pathTimeout/);
    // Capture endpoints must stay blocked before the rewrite proxy.
    expect(config).toContain('source: "/api/portfolio/history/capture"');
    expect(config).toContain('destination: "/api/capture-blocked"');
    expect(config).toContain('source: "/api/:path*"');
  });

  it("matches the installed Next.js proxyTimeout contract", () => {
    const proxyRequest = readFileSync(
      join(process.cwd(), "node_modules/next/dist/server/lib/router-utils/proxy-request.js"),
      "utf8",
    );
    expect(proxyRequest).toContain("proxyTimeout || 30000");
    expect(proxyRequest).toContain("Internal Server Error");
    const routerServer = readFileSync(
      join(process.cwd(), "node_modules/next/dist/server/lib/router-server.js"),
      "utf8",
    );
    expect(routerServer).toContain("config.experimental.proxyTimeout");
  });
});
