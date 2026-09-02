import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../../app/i18n";
import {
  createRealizedTrade,
  deleteRealizedTrade,
  fetchRealizedTrades,
  updateRealizedTrade,
} from "./realizedTradesClient";
import { RealizedGainsPage } from "./RealizedGainsPage";
import { buildSampleRealizedTrade } from "./realizedTradeTestFixtures";

vi.mock("./realizedTradesClient", async () => {
  const actual = await vi.importActual<typeof import("./realizedTradesClient")>(
    "./realizedTradesClient",
  );
  return {
    ...actual,
    fetchRealizedTrades: vi.fn(),
    createRealizedTrade: vi.fn(),
    updateRealizedTrade: vi.fn(),
    deleteRealizedTrade: vi.fn(),
  };
});

function renderPage(language: "en" | "zh-TW" = "en") {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", language);
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <RealizedGainsPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("RealizedGainsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchRealizedTrades).mockImplementation(async (currency) =>
      currency === "TWD" ? [buildSampleRealizedTrade()] : [],
    );
    vi.mocked(createRealizedTrade).mockResolvedValue(buildSampleRealizedTrade({ id: 2, symbol: "AAPL", currency: "USD" }));
    vi.mocked(updateRealizedTrade).mockResolvedValue(buildSampleRealizedTrade({ sellPrice: 1030, realizedGain: 110000 }));
    vi.mocked(deleteRealizedTrade).mockResolvedValue(undefined);
  });

  it("renders realized gains page and fetches TWD trades", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Realized P/L" })).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchRealizedTrades).toHaveBeenCalledWith("TWD");
    });
    expect(await screen.findByTestId("realized-trade-row-1")).toBeInTheDocument();
    expect(screen.getByText("NT$1,020")).toBeInTheDocument();
  });

  it("renders zh-TW core labels", async () => {
    renderPage("zh-TW");
    expect(await screen.findByRole("heading", { level: 1, name: "已實現損益" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "TWD (NT$)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "USD (US$)" })).toBeInTheDocument();
  });

  it("switches to USD and fetches the USD dataset only", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchRealizedTrades).mockImplementation(async (currency) =>
      currency === "USD"
        ? [buildSampleRealizedTrade({ id: 9, symbol: "AAPL", currency: "USD", buyPrice: 180.25, sellPrice: 195.5, shares: 10, realizedGain: 152.5 })]
        : [],
    );
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Realized P/L" })).toBeInTheDocument();

    await user.click(screen.getByTestId("realized-currency-usd"));
    await waitFor(() => {
      expect(fetchRealizedTrades).toHaveBeenCalledWith("USD");
    });
    expect(await screen.findByTestId("realized-trade-row-9")).toBeInTheDocument();
    expect(screen.getByText("US$195.5")).toBeInTheDocument();
    expect(screen.queryByText("NT$1,020")).not.toBeInTheDocument();
  });

  it("shows an empty state when the selected currency has no trades", async () => {
    vi.mocked(fetchRealizedTrades).mockResolvedValue([]);
    renderPage();
    expect(
      await screen.findByText("No TWD realized trades yet."),
    ).toBeInTheDocument();
  });

  it("creates a trade with fractional shares and uses backend gain in the table", async () => {
    const user = userEvent.setup();
    const created = buildSampleRealizedTrade({
      id: 2,
      symbol: "AAPL",
      currency: "USD",
      buyPrice: 180.25,
      sellPrice: 195.5,
      shares: 10.5,
      sellDate: "2026-08-20",
      realizedGain: 160.13,
    });
    vi.mocked(fetchRealizedTrades).mockImplementation(async (currency) =>
      currency === "USD" ? [created] : [],
    );
    vi.mocked(createRealizedTrade).mockResolvedValue(created);

    renderPage();
    await user.click(screen.getByTestId("realized-currency-usd"));
    await waitFor(() => {
      expect(fetchRealizedTrades).toHaveBeenCalledWith("USD");
    });
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "aapl" } });
    fireEvent.change(screen.getByLabelText("Buy price"), { target: { value: "180.25" } });
    fireEvent.change(screen.getByLabelText("Sell price"), { target: { value: "195.5" } });
    fireEvent.change(screen.getByLabelText("Shares"), { target: { value: "10.5" } });
    fireEvent.change(screen.getByLabelText("Sell date"), { target: { value: "2026-08-20" } });
    await user.click(screen.getByTestId("realized-submit-button"));

    await waitFor(() => {
      expect(createRealizedTrade).toHaveBeenCalled();
    });
    expect(vi.mocked(createRealizedTrade).mock.calls[0][0]).toMatchObject({
      symbol: "aapl",
      currency: "USD",
      buyPrice: 180.25,
      sellPrice: 195.5,
      shares: 10.5,
      sellDate: "2026-08-20",
    });
  });

  it("shows create failures in the form section", async () => {
    const user = userEvent.setup();
    vi.mocked(createRealizedTrade).mockRejectedValue(new Error("Network error"));
    renderPage();
    await user.click(screen.getByTestId("realized-currency-usd"));
    await waitFor(() => {
      expect(fetchRealizedTrades).toHaveBeenCalledWith("USD");
    });
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "AAPL" } });
    fireEvent.change(screen.getByLabelText("Buy price"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("Sell price"), { target: { value: "110" } });
    fireEvent.change(screen.getByLabelText("Shares"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Sell date"), { target: { value: "2026-08-20" } });
    await user.click(screen.getByTestId("realized-submit-button"));

    await waitFor(() => {
      expect(createRealizedTrade).toHaveBeenCalled();
    });
    expect(await screen.findByText("Load failed")).toBeInTheDocument();
    expect(screen.getByTestId("realized-submit-button")).not.toBeDisabled();
  });

  it("edits an existing trade", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = await screen.findByTestId("realized-trade-row-1");

    await user.click(within(row).getByRole("button", { name: "Edit" }));
    await user.clear(screen.getByLabelText("Sell price"));
    await user.type(screen.getByLabelText("Sell price"), "1030");
    await user.click(screen.getByTestId("realized-submit-button"));
    await waitFor(() => {
      expect(updateRealizedTrade).toHaveBeenCalledWith(1, expect.objectContaining({ sellPrice: 1030 }));
    });
  });

  it("deletes a trade after confirmation", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("realized-trade-row-1");
    await user.click(screen.getByTestId("realized-delete-button-1"));
    await user.click(await screen.findByTestId("realized-delete-confirm"));
    await waitFor(() => {
      expect(deleteRealizedTrade).toHaveBeenCalled();
    });
  });

  it("canceling delete keeps the trade row", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("realized-trade-row-1");
    await user.click(screen.getByTestId("realized-delete-button-1"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteRealizedTrade).not.toHaveBeenCalled();
    expect(screen.getByTestId("realized-trade-row-1")).toBeInTheDocument();
  });

  it("shows delete failures inside the confirmation dialog", async () => {
    const user = userEvent.setup();
    vi.mocked(deleteRealizedTrade).mockRejectedValue(new Error("Network error"));
    renderPage();
    await screen.findByTestId("realized-trade-row-1");
    await user.click(screen.getByTestId("realized-delete-button-1"));
    await user.click(screen.getByTestId("realized-delete-confirm"));

    await waitFor(() => {
      expect(deleteRealizedTrade).toHaveBeenCalled();
    });
    expect(screen.getByText("Delete this realized trade record?")).toBeInTheDocument();
    expect(await screen.findByText("Load failed")).toBeInTheDocument();
    expect(screen.getByTestId("realized-trade-row-1")).toBeInTheDocument();
  });
});
