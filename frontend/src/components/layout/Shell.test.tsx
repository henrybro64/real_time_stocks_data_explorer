import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../../app/i18n";
import { Shell } from "./Shell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/portfolio",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("../../services/api/auth", () => ({
  fetchAuthStatus: vi.fn().mockResolvedValue({
    authenticated: false,
    authEnabled: false,
    localAuthEnabled: false,
    username: null,
  }),
  logout: vi.fn(),
}));

describe("Shell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem("stock-ticker-language", "en");
  });

  it("links realized gains between portfolio and assets", async () => {
    render(
      <LanguageProvider>
        <Shell>
          <div>content</div>
        </Shell>
      </LanguageProvider>,
    );

    const realizedLink = await screen.findByRole("link", { name: "Realized P/L" });
    expect(realizedLink).toHaveAttribute("href", "/realized-gains");

    const navLinks = screen.getAllByRole("link").map((link) => link.textContent);
    const portfolioIndex = navLinks.indexOf("Portfolio");
    const realizedIndex = navLinks.indexOf("Realized P/L");
    const assetsIndex = navLinks.indexOf("Assets");
    expect(portfolioIndex).toBeGreaterThanOrEqual(0);
    expect(realizedIndex).toBeGreaterThan(portfolioIndex);
    expect(assetsIndex).toBeGreaterThan(realizedIndex);
  });
});
