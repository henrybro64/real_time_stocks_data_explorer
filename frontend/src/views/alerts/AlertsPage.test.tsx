import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AlertEvent, AlertRule } from "@shared/api";
import { render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import {
  createAlertRule,
  deleteAlertRule,
  fetchAlertEvents,
  fetchAlertRules,
  searchStocks,
  updateAlertRule,
  ApiError,
} from "../../services/api/client";
import { AlertsPage } from "./AlertsPage";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

vi.mock("../../services/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../services/api/client")>("../../services/api/client");
  return {
    ...actual,
    fetchAlertRules: vi.fn(),
    fetchAlertEvents: vi.fn(),
    createAlertRule: vi.fn(),
    updateAlertRule: vi.fn(),
    deleteAlertRule: vi.fn(),
    searchStocks: vi.fn(),
  };
});

function buildRule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: 1,
    stockId: "US:AAPL",
    stock: {
      id: "US:AAPL",
      market: "US",
      symbol: "AAPL",
      name: "Apple Inc.",
      exchange: "NASDAQ",
      currency: "USD",
      timezone: "America/New_York",
      nameEn: "Apple Inc.",
      nameZhTw: "蘋果",
    },
    ruleType: "price_above",
    thresholdValue: 200,
    enabled: true,
    lastConditionMet: true,
    lastObservedPrice: 205,
    lastObservedQuoteTimestamp: "2026-05-26T09:30:00-04:00",
    lastEvaluatedAt: "2026-05-26T09:30:00-04:00",
    lastTriggeredAt: "2026-05-26T09:30:00-04:00",
    createdAt: "2026-05-26T09:00:00-04:00",
    updatedAt: "2026-05-26T09:30:00-04:00",
    ...overrides,
  };
}

function buildEvent(overrides: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: 11,
    alertRuleId: 1,
    stockId: "US:AAPL",
    stock: buildRule().stock,
    ruleType: "price_above",
    thresholdValue: 200,
    triggeredAt: "2026-05-26T09:30:00-04:00",
    observedPrice: 205,
    quoteTimestamp: "2026-05-26T09:30:00-04:00",
    message: "AAPL rose above 200.00 (observed 205.00).",
    ...overrides,
  };
}

function renderAlertsPage(props: { initialStockId?: string } = {}) {
  window.localStorage.clear();
  window.localStorage.setItem("stock-ticker-language", "en");

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <AlertsPage initialStockId={props.initialStockId} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("AlertsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchAlertRules).mockResolvedValue([buildRule()]);
    vi.mocked(fetchAlertEvents).mockResolvedValue([buildEvent()]);
    vi.mocked(createAlertRule).mockResolvedValue(buildRule());
    vi.mocked(updateAlertRule).mockResolvedValue(buildRule());
    vi.mocked(deleteAlertRule).mockResolvedValue(undefined);
    vi.mocked(searchStocks).mockResolvedValue([buildRule().stock!]);
  });

  it("renders alert rules and recent events", async () => {
    renderAlertsPage();

    expect(await screen.findByRole("heading", { level: 1, name: "Threshold alerts" })).toBeInTheDocument();
    expect(await screen.findByTestId("alerts-rule-row-1")).toBeInTheDocument();
    expect(await screen.findByTestId("alerts-event-row-11")).toBeInTheDocument();
    expect(screen.getAllByText("AAPL")).toHaveLength(2);
    expect(screen.getByText("AAPL rose above 200.00 (observed 205.00).")).toBeInTheDocument();
  });

  it("submits a new alert rule from a prefilled stock id", async () => {
    const user = userEvent.setup();
    const createdRule = buildRule({ id: 2, thresholdValue: 210, lastConditionMet: false, lastObservedPrice: null });

    vi.mocked(fetchAlertRules)
      .mockResolvedValueOnce([])
      .mockResolvedValue([createdRule]);
    vi.mocked(fetchAlertEvents).mockResolvedValue([]);
    vi.mocked(createAlertRule).mockResolvedValue(createdRule);

    renderAlertsPage({ initialStockId: "US:AAPL" });

    expect(await screen.findByRole("heading", { level: 1, name: "Threshold alerts" })).toBeInTheDocument();
    expect(screen.getByLabelText("Stock symbol")).toHaveValue("US:AAPL");

    await user.clear(screen.getByLabelText("Threshold price"));
    await user.type(screen.getByLabelText("Threshold price"), "210");
    await user.click(screen.getByTestId("alert-submit-button"));

    await waitFor(() => {
      expect(createAlertRule).toHaveBeenCalledWith({
        stockId: "US:AAPL",
        ruleType: "price_above",
        thresholdValue: 210,
        enabled: true,
      });
    });

    expect(await screen.findByTestId("alerts-rule-row-2")).toBeInTheDocument();
  });

  it("resolves bare and lowercase tickers to canonical stock ids before create", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAlertRules).mockResolvedValue([]);
    vi.mocked(fetchAlertEvents).mockResolvedValue([]);
    vi.mocked(searchStocks).mockResolvedValue([
      {
        id: "US:AAPL",
        market: "US",
        symbol: "AAPL",
        name: "Apple Inc.",
        exchange: "NASDAQ",
        currency: "USD",
        timezone: "America/New_York",
        nameEn: "Apple Inc.",
        nameZhTw: "蘋果",
      },
    ]);

    renderAlertsPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Threshold alerts" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Stock symbol"), "aapl");
    await user.type(screen.getByLabelText("Threshold price"), "200");
    await user.click(screen.getByTestId("alert-submit-button"));

    await waitFor(() => {
      expect(createAlertRule).toHaveBeenCalledWith({
        stockId: "US:AAPL",
        ruleType: "price_above",
        thresholdValue: 200,
        enabled: true,
      });
    });
  });

  it("resolves Taiwan numeric tickers to TW: prefix before create", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAlertRules).mockResolvedValue([]);
    vi.mocked(fetchAlertEvents).mockResolvedValue([]);

    renderAlertsPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Threshold alerts" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Stock symbol"), "2330");
    await user.type(screen.getByLabelText("Threshold price"), "800");
    await user.click(screen.getByTestId("alert-submit-button"));

    await waitFor(() => {
      expect(createAlertRule).toHaveBeenCalledWith({
        stockId: "TW:2330",
        ruleType: "price_above",
        thresholdValue: 800,
        enabled: true,
      });
    });
    expect(searchStocks).not.toHaveBeenCalled();
  });

  it("shows field-level validation instead of load-failed for unknown tickers", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAlertRules).mockResolvedValue([]);
    vi.mocked(fetchAlertEvents).mockResolvedValue([]);
    vi.mocked(searchStocks).mockResolvedValue([]);
    vi.mocked(createAlertRule).mockRejectedValue(
      new ApiError("Bad Request", {
        status: 400,
        kind: "validation",
        detail: "Unknown stock id: US:ZZZZZ",
      }),
    );

    renderAlertsPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Threshold alerts" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Stock symbol"), "ZZZZZ");
    await user.type(screen.getByLabelText("Threshold price"), "200");
    await user.click(screen.getByTestId("alert-submit-button"));

    expect(
      await screen.findByText(
        "Stock not found: US:ZZZZZ. Enter a ticker or choose a canonical ID such as US:AAPL.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Could not save")).not.toBeInTheDocument();
    expect(screen.queryByText("Load failed")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Stock symbol")).toHaveValue("US:ZZZZZ");
    expect(screen.getByLabelText("Threshold price")).toHaveValue(200);
  });

  it("shows field-level validation for ambiguous symbols without a save banner", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAlertRules).mockResolvedValue([]);
    vi.mocked(fetchAlertEvents).mockResolvedValue([]);
    vi.mocked(searchStocks).mockResolvedValue([
      {
        id: "TW:1111",
        market: "TW",
        symbol: "FOO",
        name: "Foo One",
        exchange: "TWSE",
        currency: "TWD",
        timezone: "Asia/Taipei",
        nameEn: "Foo One",
        nameZhTw: "Foo One",
      },
      {
        id: "TW:2222",
        market: "TW",
        symbol: "FOO",
        name: "Foo Two",
        exchange: "TWSE",
        currency: "TWD",
        timezone: "Asia/Taipei",
        nameEn: "Foo Two",
        nameZhTw: "Foo Two",
      },
    ]);

    renderAlertsPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Threshold alerts" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Stock symbol"), "FOO");
    await user.type(screen.getByLabelText("Threshold price"), "200");
    await user.click(screen.getByTestId("alert-submit-button"));

    expect(
      await screen.findByText('Ambiguous symbol "FOO". Choose one of: TW:1111, TW:2222'),
    ).toBeInTheDocument();
    expect(createAlertRule).not.toHaveBeenCalled();
    expect(screen.queryByText("Could not save")).not.toBeInTheDocument();
    expect(screen.queryByText("Load failed")).not.toBeInTheDocument();
  });

  it("keeps a save banner for non-stock validation failures", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAlertRules).mockResolvedValue([]);
    vi.mocked(fetchAlertEvents).mockResolvedValue([]);
    vi.mocked(searchStocks).mockResolvedValue([
      {
        id: "US:AAPL",
        market: "US",
        symbol: "AAPL",
        name: "Apple Inc.",
        exchange: "NASDAQ",
        currency: "USD",
        timezone: "America/New_York",
        nameEn: "Apple Inc.",
        nameZhTw: "蘋果",
      },
    ]);
    vi.mocked(createAlertRule).mockRejectedValue(
      new ApiError("Bad Request", {
        status: 400,
        kind: "validation",
        detail: "Threshold value must be greater than 0",
      }),
    );

    renderAlertsPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Threshold alerts" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Stock symbol"), "AAPL");
    await user.type(screen.getByLabelText("Threshold price"), "200");
    await user.click(screen.getByTestId("alert-submit-button"));

    expect(await screen.findByText("Could not save")).toBeInTheDocument();
    expect(screen.getByText("Threshold value must be greater than 0")).toBeInTheDocument();
  });

  it("supports editing and deleting an existing alert rule", async () => {
    const user = userEvent.setup();
    const updatedRule = buildRule({
      thresholdValue: 215,
      lastConditionMet: false,
      lastObservedPrice: 214.5,
    });

    vi.mocked(updateAlertRule).mockResolvedValue(updatedRule);
    vi.mocked(fetchAlertRules)
      .mockResolvedValueOnce([buildRule()])
      .mockResolvedValue([updatedRule]);

    renderAlertsPage();

    const ruleRow = await screen.findByTestId("alerts-rule-row-1");
    await user.click(within(ruleRow).getByRole("button", { name: "Edit" }));
    await user.clear(screen.getByLabelText("Threshold price"));
    await user.type(screen.getByLabelText("Threshold price"), "215");
    await user.click(screen.getByTestId("alert-submit-button"));

    await waitFor(() => {
      expect(updateAlertRule).toHaveBeenCalledWith(1, {
        stockId: "US:AAPL",
        ruleType: "price_above",
        thresholdValue: 215,
        enabled: true,
      });
    });

    await user.click(within(ruleRow).getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      expect(deleteAlertRule).toHaveBeenCalled();
    });
    expect(vi.mocked(deleteAlertRule).mock.calls[0][0]).toBe(1);
  });
});
