import { defineConfig } from "@playwright/test";
import path from "node:path";


const frontendDir = __dirname;
const repoRoot = path.resolve(frontendDir, "..");
const backendDir = path.join(repoRoot, "backend");
const databaseUrl = process.env.DATABASE_URL ?? "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks_test";
const skipWebServer = process.env.PLAYWRIGHT_SKIP_WEBSERVER === "1";


export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
  },
  webServer: skipWebServer
    ? undefined
    : [
        {
          command: "python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
          cwd: backendDir,
          env: {
            ...process.env,
            DATABASE_URL: databaseUrl,
            TW_USE_MOCK_QUOTES: "true",
          },
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          url: "http://127.0.0.1:8000/health",
        },
        {
          command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
          cwd: frontendDir,
          env: {
            ...process.env,
            BACKEND_PROXY_URL: "http://127.0.0.1:8000",
            NEXT_PUBLIC_API_PREFIX: "/api",
          },
          reuseExistingServer: !process.env.CI,
          timeout: 180_000,
          url: "http://127.0.0.1:3000",
        },
      ],
});
