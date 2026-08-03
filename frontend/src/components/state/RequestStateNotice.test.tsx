import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "./RequestStateNotice";
import { ApiError } from "../../services/api/client";

function renderWithLanguage(ui: React.ReactElement, language = "zh-TW") {
  window.localStorage.setItem("stock-ticker-language", language);
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

  it("maps validation ApiErrors to a save notice and surfaces detail", () => {
    expect(
      errorToNoticeKind(
        new ApiError("Request failed", {
          kind: "validation",
          status: 400,
          detail: "Unknown stock id: AAPL",
        }),
      ),
    ).toBe("validation");

    renderWithLanguage(
      <RequestStateNotice
        kind="validation"
        scopeLabel="門檻警示"
        detail="Unknown stock id: AAPL"
      />,
    );

    expect(screen.getByText("無法儲存")).toBeInTheDocument();
    expect(screen.getByText("Unknown stock id: AAPL")).toBeInTheDocument();
    expect(screen.queryByText("載入失敗")).not.toBeInTheDocument();
  });

  it("keeps generic load-failure copy for unexpected errors", () => {
    renderWithLanguage(<RequestStateNotice kind="error" scopeLabel="門檻警示" />, "zh-TW");
    expect(screen.getByText("載入失敗")).toBeInTheDocument();
  });

  it("surfaces official schema and rate-limit messaging from detail text", () => {
    renderWithLanguage(
      <RequestStateNotice
        kind="unavailable"
        scopeLabel="資券變化"
        detail="Taiwan margin-short official response could not be interpreted."
      />,
      "zh-TW",
    );
    expect(screen.getByText("官方資料格式無法解析")).toBeInTheDocument();
    expect(screen.getByText(/回應格式無法判讀/)).toBeInTheDocument();

    renderWithLanguage(
      <RequestStateNotice
        kind="unavailable"
        scopeLabel="資券變化"
        detail="Taiwan margin-short official source is rate-limited."
      />,
      "en",
    );
    expect(screen.getByText("Official source rate-limited")).toBeInTheDocument();
  });

  it("uses official access-denied wording instead of app permission copy", () => {
    renderWithLanguage(
      <RequestStateNotice
        kind="permission"
        scopeLabel="Margin / short changes"
        detail="Taiwan margin-short official source denied access."
      />,
      "en",
    );
    expect(screen.getByText("Official source denied access")).toBeInTheDocument();
    expect(screen.getByText(/not a site sign-in problem/)).toBeInTheDocument();
  });
});
