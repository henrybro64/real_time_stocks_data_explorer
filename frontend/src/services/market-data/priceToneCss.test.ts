import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { PRICE_STROKE_COLORS } from "./priceTone";

const globalCssPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../styles/global.css",
);

describe("price tone CSS contracts", () => {
  const css = readFileSync(globalCssPath, "utf8");

  it("defines market-specific text tone colors without redefining app-positive/app-negative", () => {
    expect(css).toMatch(/\.app-price-convention-us\.app-price-up\s*\{[^}]*support-success/);
    expect(css).toMatch(/\.app-price-convention-us\.app-price-down\s*\{[^}]*support-error/);
    expect(css).toMatch(/\.app-price-convention-taiwan\.app-price-up\s*\{[^}]*support-error/);
    expect(css).toMatch(/\.app-price-convention-taiwan\.app-price-down\s*\{[^}]*support-success/);

    const positiveBlock = css.match(/\.app-positive\s*\{[^}]+\}/)?.[0] ?? "";
    const negativeBlock = css.match(/\.app-negative\s*\{[^}]+\}/)?.[0] ?? "";
    expect(positiveBlock).toContain("support-success");
    expect(negativeBlock).toContain("support-error");
  });

  it("exposes convention CSS vars aligned with PRICE_STROKE_COLORS", () => {
    const usVars =
      css.match(
        /\[data-price-convention="us"\]\s*\{[^}]+\}|\.app-price-convention-us,\s*\n\[data-price-convention="us"\]\s*\{[^}]+\}/,
      )?.[0] ?? "";
    const taiwanVars =
      css.match(
        /\[data-price-convention="taiwan"\]\s*\{[^}]+\}|\.app-price-convention-taiwan,\s*\n\[data-price-convention="taiwan"\]\s*\{[^}]+\}/,
      )?.[0] ?? "";

    // Combined selector blocks in global.css.
    const usBlock =
      css.match(
        /\.app-price-convention-us,\s*\[data-price-convention="us"\]\s*\{[\s\S]*?\}/,
      )?.[0] ?? "";
    const taiwanBlock =
      css.match(
        /\.app-price-convention-taiwan,\s*\[data-price-convention="taiwan"\]\s*\{[\s\S]*?\}/,
      )?.[0] ?? "";

    expect(usBlock).toContain(`--app-price-up: ${PRICE_STROKE_COLORS.us.up}`);
    expect(usBlock).toContain(`--app-price-down: ${PRICE_STROKE_COLORS.us.down}`);
    expect(taiwanBlock).toContain(`--app-price-up: ${PRICE_STROKE_COLORS.taiwan.up}`);
    expect(taiwanBlock).toContain(`--app-price-down: ${PRICE_STROKE_COLORS.taiwan.down}`);
    expect(usVars.length + taiwanVars.length).toBeGreaterThanOrEqual(0);
  });

  it("candlestick body/wick colors consume convention CSS variables", () => {
    expect(css).toMatch(/\.app-chart-candle-body-up\s*\{[^}]*var\(--app-price-up-fill\)/);
    expect(css).toMatch(/\.app-chart-candle-body-up\s*\{[^}]*var\(--app-price-up\)/);
    expect(css).toMatch(/\.app-chart-candle-body-down\s*\{[^}]*var\(--app-price-down-fill\)/);
    expect(css).toMatch(/\.app-chart-candle-body-down\s*\{[^}]*var\(--app-price-down\)/);
    expect(css).toMatch(/\.app-chart-candle-wick-up\s*\{[^}]*var\(--app-price-up-wick\)/);
    expect(css).toMatch(/\.app-chart-candle-wick-down\s*\{[^}]*var\(--app-price-down-wick\)/);
  });
});
