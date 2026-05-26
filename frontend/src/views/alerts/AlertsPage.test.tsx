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
  updateAlertRule,
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
    expect(screen.getByLabelText("Stock ID")).toHaveValue("US:AAPL");

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
