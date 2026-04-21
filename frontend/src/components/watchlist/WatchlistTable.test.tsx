import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { WatchlistTable } from "./WatchlistTable";

function renderWithLanguage(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

describe("WatchlistTable", () => {
  it("keeps pending tracked symbols visible when quote hydration fails", async () => {
    const onRemove = vi.fn();
    const user = userEvent.setup();

    renderWithLanguage(
      <WatchlistTable
        items={[]}
        noticeKind="error"
        onRemove={onRemove}
        pendingIds={["TW:2330"]}
        trackedCount={1}
      />,
    );

    expect(screen.getByText("載入失敗")).toBeInTheDocument();
    expect(screen.getByTestId("stock-card-TW:2330")).toBeInTheDocument();

    await user.click(screen.getByTestId("stock-card-toggle-TW:2330"));

    expect(onRemove).toHaveBeenCalledWith("TW:2330");
  });

  it("shows only the notice when there are no hydrated or pending watchlist entries", () => {
    renderWithLanguage(
      <WatchlistTable
        items={[]}
        noticeKind="unavailable"
        onRemove={() => {}}
        pendingIds={[]}
        trackedCount={0}
      />,
    );

    expect(screen.getByText("資料來源暫時不可用")).toBeInTheDocument();
    expect(
      screen.queryByText("搜尋股票後加入清單，這裡會持續顯示最新報價與更新時間。"),
    ).not.toBeInTheDocument();
  });
});
