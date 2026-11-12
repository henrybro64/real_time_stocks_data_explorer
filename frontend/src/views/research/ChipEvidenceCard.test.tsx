import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ChipEvidenceCard, type ChipEvidenceRow } from "./ChipEvidenceCard";

const okRow: ChipEvidenceRow = {
  stockId: "TW:2330",
  stockName: "台積電",
  nameStatus: "ok",
  institutional: {
    status: "ok",
    sourceAsOf: "2024-06-24",
    sessions: [
      {
        date: "2024-06-20",
        foreignNetLots: 10,
        trustNetLots: 1,
        dealerNetLots: -1,
        totalNetLots: 10,
      },
      {
        date: "2024-06-21",
        foreignNetLots: 11,
        trustNetLots: 1,
        dealerNetLots: -1,
        totalNetLots: 11,
      },
      {
        date: "2024-06-22",
        foreignNetLots: 12,
        trustNetLots: 1,
        dealerNetLots: -1,
        totalNetLots: 12,
      },
      {
        date: "2024-06-23",
        foreignNetLots: 13,
        trustNetLots: 1,
        dealerNetLots: -1,
        totalNetLots: 13,
      },
      {
        date: "2024-06-24",
        foreignNetLots: 14,
        trustNetLots: 1,
        dealerNetLots: -1,
        totalNetLots: 14,
      },
    ],
    fiveSessionTotalsStatus: "ok",
    fiveSessionTotals: {
      foreignNetLots: 60,
      trustNetLots: 5,
      dealerNetLots: -5,
      totalNetLots: 60,
      sessionCount: 5,
    },
  },
  margin: {
    status: "ok",
    sourceAsOf: "2024-06-24",
    balanceLots: 100,
    balanceDeltaLotsApprox5Sessions: 5,
    direction: "increase",
    shortBalanceLots: 0,
    shortBalanceStatus: "ok",
    utilization: { status: "unavailable", reason: "unverified" },
  },
  tdcc: {
    status: "ok",
    holdersGe400LotsPercent: 41.2,
    weeklyChangePercentagePoints: 0.5,
    priorWeekDate: "2024-06-14",
  },
  notBuySignal: true,
};

describe("ChipEvidenceCard", () => {
  it("renders institutional sessions, margin direction, and TDCC percentage points", () => {
    render(
      <LanguageProvider>
        <ChipEvidenceCard row={okRow} />
      </LanguageProvider>,
    );

    expect(screen.getByTestId("chip-evidence-card")).toBeInTheDocument();
    expect(screen.getByTestId("chip-evidence-institutional-sessions")).toBeInTheDocument();
    expect(screen.getByTestId("chip-evidence-institutional-totals")).toHaveTextContent(
      /5-session totals|五日合計/i,
    );
    expect(screen.getByText(/increase/i)).toBeInTheDocument();
    expect(screen.getByText(/weekly Δ \(pp\)|週變化（百分點）/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Utilization: unavailable|融資使用率: unavailable/i),
    ).toBeInTheDocument();
    // Zero short balance is a real value.
    expect(screen.getByText(/Short balance: 0|融券: 0/i)).toBeInTheDocument();
  });

  it("renders partial totals banner from backend row", () => {
    const row: ChipEvidenceRow = {
      ...okRow,
      institutional: {
        ...okRow.institutional,
        fiveSessionTotalsStatus: "partial",
        fiveSessionTotalsReason: "null_lots_in_session_window",
        fiveSessionTotals: {
          foreignNetLots: 48,
          trustNetLots: 5,
          dealerNetLots: -5,
          totalNetLots: 48,
          note: "Partial totals omit null sessions; not invented zeros.",
        },
      },
    };
    render(
      <LanguageProvider>
        <ChipEvidenceCard row={row} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("chip-evidence-institutional-totals")).toHaveTextContent(
      /partial|部分/i,
    );
  });

  it("surfaces stale and not_fetched provenance honestly", () => {
    const row: ChipEvidenceRow = {
      stockId: "TW:2317",
      stockName: "鴻海",
      institutional: {
        status: "stale",
        stale: true,
        sourceAsOf: "2024-06-01",
        capturedAt: "2024-06-01T08:00:00+00:00",
        reason: "provider_stale",
        fiveSessionTotalsStatus: "partial",
        fiveSessionTotals: null,
      },
      margin: {
        status: "not_fetched",
        reason: "field_absent",
      },
      tdcc: {
        status: "stale",
        stale: true,
        holdersGe400LotsPercent: 20,
      },
    };

    render(
      <LanguageProvider>
        <ChipEvidenceCard row={row} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("chip-evidence-institutional-status")).toHaveTextContent(
      /stale|已過期/i,
    );
    expect(screen.getByTestId("chip-evidence-margin-status")).toHaveTextContent(
      /not fetched|尚未抓取/i,
    );
  });

  it("renders freshness unverified when staleCheck is calendar_unavailable", () => {
    const row: ChipEvidenceRow = {
      ...okRow,
      institutional: {
        ...okRow.institutional,
        status: "ok",
        staleCheck: "calendar_unavailable",
        staleBasis: "unverified",
      },
      margin: {
        ...okRow.margin,
        status: "ok",
        staleCheck: "calendar_unavailable",
        staleBasis: "unverified",
      },
    };
    render(
      <LanguageProvider>
        <ChipEvidenceCard row={row} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("chip-evidence-institutional-status")).toHaveTextContent(
      /freshness unverified \(trading calendar unavailable\)|新鮮度未驗證（交易日曆不可用）/,
    );
    expect(screen.getByTestId("chip-evidence-margin-status")).toHaveTextContent(
      /freshness unverified \(trading calendar unavailable\)|新鮮度未驗證（交易日曆不可用）/,
    );
    expect(screen.getByTestId("chip-evidence-institutional-status")).not.toHaveTextContent(
      /stale \(showing last verified\)|已過期（顯示最後驗證結果）/,
    );
  });

  it("renders freshness unverified when staleCheck is capture_time_unavailable", () => {
    const row: ChipEvidenceRow = {
      ...okRow,
      institutional: {
        ...okRow.institutional,
        status: "ok",
        staleCheck: "capture_time_unavailable",
        staleBasis: "unverified",
      },
      margin: {
        ...okRow.margin,
        status: "ok",
        staleCheck: "capture_time_unavailable",
        staleBasis: "unverified",
      },
    };
    render(
      <LanguageProvider>
        <ChipEvidenceCard row={row} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("chip-evidence-institutional-status")).toHaveTextContent(
      /freshness unverified \(capture time unavailable\)|新鮮度未驗證（缺少擷取時間）/,
    );
    expect(screen.getByTestId("chip-evidence-margin-status")).toHaveTextContent(
      /freshness unverified \(capture time unavailable\)|新鮮度未驗證（缺少擷取時間）/,
    );
    expect(screen.getByTestId("chip-evidence-institutional-status")).not.toHaveTextContent(
      /stale \(showing last verified\)|已過期（顯示最後驗證結果）/,
    );
  });

  it("shows stale banner when refetch failed but cached data exists", () => {
    render(
      <LanguageProvider>
        <ChipEvidenceCard
          row={okRow}
          staleCacheBanner={{ capturedAt: "2024-06-24T08:00:00+00:00" }}
        />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("chip-evidence-stale-banner")).toHaveTextContent(
      /stale — last verified|已過期 — 最後驗證/i,
    );
  });
});
