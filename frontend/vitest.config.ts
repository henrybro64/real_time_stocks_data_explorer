import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const allureResultsDir = process.env.ALLURE_RESULTS_DIR;
const setupFiles = allureResultsDir
  ? ["allure-vitest/setup", "./vitest.setup.ts"]
  : ["./vitest.setup.ts"];
const reporters = allureResultsDir
  ? ["default", ["allure-vitest/reporter", { resultsDir: allureResultsDir }]]
  : ["default"];

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@shared": path.resolve(__dirname, "../packages/shared-contracts"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    exclude: ["tests/e2e/**"],
    include: ["src/**/*.{test,spec}.{ts,tsx}", "tests/unit/**/*.{test,spec}.{ts,tsx}"],
    reporters,
    setupFiles,
  },
});
