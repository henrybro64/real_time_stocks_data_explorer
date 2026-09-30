import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../../styles/global.css"), "utf8");
const page = readFileSync(resolve(__dirname, "./PortfolioPage.tsx"), "utf8");

describe("portfolio market section layout", () => {
  it("keeps grouped holdings inside the tile and scrolls the wide table", () => {
    expect(page).toContain('className="app-column-spacing app-portfolio-holdings-column"');
    expect(page).toContain('className="app-section-tile app-portfolio-holdings-tile"');
    expect(page).toContain('className="app-portfolio-market-sections"');
    expect(page).not.toMatch(/app-stock-analysis-body"[\s\S]{0,400}portfolio-market-section-tw/);
    expect(css).toMatch(
      /\.app-portfolio-holdings-column \{\s*min-width: 0;\s*max-width: 100%;\s*\}/,
    );
    expect(css).toMatch(
      /\.app-portfolio-holdings-tile \{\s*min-width: 0;\s*max-width: 100%;\s*overflow-x: clip;\s*\}/,
    );
    expect(css).toMatch(
      /\.app-portfolio-table-wrap \{\s*width: 100%;\s*max-width: 100%;\s*min-width: 0;\s*overflow-x: auto;/,
    );
    expect(css).toContain(".app-portfolio-table-wrap .cds--data-table {\n  min-width: 72rem;\n}");
  });
});
