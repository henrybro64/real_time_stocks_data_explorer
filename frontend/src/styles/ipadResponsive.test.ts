import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "./global.css"), "utf8");

describe("iPad responsive layout contracts", () => {
  it("defines the tablet and desktop breakpoint bands from issue #98", () => {
    expect(css).toContain("@media (max-width: 767px)");
    expect(css).toContain("@media (min-width: 768px) and (max-width: 1023px)");
    expect(css).toContain("@media (min-width: 1024px) and (max-width: 1279px)");
    expect(css).toContain("@media (min-width: 1280px)");
  });

  it("uses responsive watchlist column counts for tablet and desktop", () => {
    expect(css).toMatch(
      /@media \(min-width: 768px\) and \(max-width: 1023px\)[\s\S]*\.app-watchlist-grid \{\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/,
    );
    expect(css).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1279px\)[\s\S]*\.app-watchlist-grid \{\s*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);/,
    );
  });

  it("keeps chart heights in the iPad-comfortable range", () => {
    expect(css).toContain("--app-chart-height: 26.25rem;");
    expect(css).toContain("--app-chart-height: 28.75rem;");
    expect(css).toContain("--app-chart-height: 32.5rem;");
  });

  it("applies safe-area and modern viewport height fallbacks", () => {
    expect(css).toContain("--app-safe-left: env(safe-area-inset-left, 0px);");
    expect(css).toContain("--app-safe-right: env(safe-area-inset-right, 0px);");
    expect(css).toContain("@supports (height: 100dvh)");
    expect(css).toContain("touch-action: pan-y;");
  });

  it("keeps ranking category controls sticky for tablet browsing", () => {
    expect(css).toContain(".app-rankings-controls");
    expect(css).toMatch(
      /@media \(min-width: 768px\)[\s\S]*\.app-rankings-controls \{\s*position: sticky;/,
    );
  });

  it("stacks stock-detail chart axes correctly on tablet widths", () => {
    expect(css).toMatch(
      /\.app-stock-detail-chart \.app-chart-x-axis \{\s*grid-column: 1;\s*grid-row: 3;/,
    );
    expect(css).toMatch(
      /\.app-stock-detail-chart \.app-chart-y-ticks \{\s*flex-direction: row;[\s\S]*min-height: 0;/,
    );
  });
});
