import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { RequestStateNotice } from "./RequestStateNotice";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

describe("RequestStateNotice", () => {
  it("renders an auth message with a login action", () => {
    renderWithLanguage(<RequestStateNotice kind="auth" scopeLabel="Watchlist" />);

    expect(screen.getByText("需要登入")).toBeInTheDocument();
    expect(screen.getByText("請先登入，才能讀取Watchlist。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往登入" })).toHaveAttribute("href", "/login");
  });

  it("renders a stale-data warning", () => {
    renderWithLanguage(<RequestStateNotice kind="stale" scopeLabel="Price history" />);

    expect(screen.getByText("顯示較舊資料")).toBeInTheDocument();
    expect(screen.getByText("最新重新整理失敗，目前顯示的是先前已載入的Price history。")).toBeInTheDocument();
  });
});
