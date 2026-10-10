import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = dirname(fileURLToPath(import.meta.url));
const monorepoRoot = join(projectRoot, "..");
const sharedContractsRoot = join(monorepoRoot, "packages", "shared-contracts");

/** FastAPI origin for dev/proxy; same-origin UI uses `/api` in the browser. */
const backendProxyUrl = (
  process.env.BACKEND_PROXY_URL ||
  process.env.BACKEND_URL ||
  "http://127.0.0.1:8000"
).replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Shared contracts live next to frontend/ in the monorepo (and Docker image).
  outputFileTracingRoot: monorepoRoot,
  turbopack: {
    // Allow resolving ../packages/shared-contracts during `next dev`.
    root: monorepoRoot,
    resolveAlias: {
      "@shared": sharedContractsRoot,
    },
  },
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "@shared": sharedContractsRoot,
    };
    return config;
  },
  async rewrites() {
    // Proxy browser API traffic to FastAPI. Internal capture endpoints stay
    // off this path so LAN hostname access cannot invoke them (#267).
    return {
      beforeFiles: [
        {
          source: "/api/portfolio/history/capture",
          destination: "/api/capture-blocked",
        },
        {
          source: "/api/rankings/snapshots/capture",
          destination: "/api/capture-blocked",
        },
        {
          source: "/api/rankings/archive/capture",
          destination: "/api/capture-blocked",
        },
        {
          source: "/api/taiwan-market-data/capture",
          destination: "/api/capture-blocked",
        },
      ],
      afterFiles: [
        {
          source: "/api/:path*",
          destination: `${backendProxyUrl}/api/:path*`,
        },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
