import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { ChipEvidenceCard, formatChipEvidenceFromFields } from "./ChipEvidenceCard";

const fiveSessions = [
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
];

describe("ChipEvidenceCard", () => {
  it("renders institutional sessions, margin direction, and TDCC percentage points", () => {
    const row = formatChipEvidenceFromFields("TW:2330", "台積電", "ok", {
      institutional_5d: {
        status: "ok",
        source_as_of: "2024-06-24",
        value: fiveSessions,
      },
      margin: {
        status: "ok",
        source_as_of: "2024-06-24",
        value: {
          balanceLots: 100,
          balanceDeltaLotsApprox5Sessions: 5,
        },
      },
      margin_utilization: {
        status: "unavailable",
        reason: "numerator_and_denominator_not_both_verified",
      },
      tdcc_holders_ge_400_lots: {
        status: "ok",
        source_as_of: "2024-06-21",
        value: {
          percent: 41.2,
          weeklyChangePercentPoints: 0.5,
          priorWeekDate: "2024-06-14",
        },
      },
    });

    render(
      <LanguageProvider>
        <ChipEvidenceCard row={row} />
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
  });

  it("matches backend partial totals when a session lot is null (#346 P0)", () => {
    const sessions = fiveSessions.map((row) => ({ ...row }));
    sessions[2] = { ...sessions[2], foreignNetLots: null as unknown as number };
    const row = formatChipEvidenceFromFields("TW:2330", "台積電", "ok", {
      institutional_5d: {
        status: "partial",
        source_as_of: "2024-06-24",
        value: sessions,
      },
      margin: { status: "unavailable" },
      tdcc_holders_ge_400_lots: { status: "unavailable" },
    });

    expect(row.institutional?.fiveSessionTotalsStatus).toBe("partial");
    expect(row.institutional?.fiveSessionTotalsReason).toBe("null_lots_in_session_window");
    // Omit null session (12); do not invent zero.
    expect(row.institutional?.fiveSessionTotals?.foreignNetLots).toBe(10 + 11 + 13 + 14);

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
    const row = formatChipEvidenceFromFields("TW:2317", "鴻海", "ok", {
      institutional_5d: {
        status: "stale",
        stale: true,
        source_as_of: "2024-06-01",
        captured_at: "2024-06-01T08:00:00+00:00",
        reason: "provider_stale",
        value: fiveSessions.slice(0, 2),
      },
      margin: {
        status: "not_fetched",
        reason: "deferred_to_next_window",
      },
      tdcc_holders_ge_400_lots: {
        status: "stale",
        value: { percent: 20, stale: true },
      },
    });

    expect(row.institutional?.fiveSessionTotalsStatus).toBe("partial");
    expect(row.institutional?.fiveSessionTotals).toBeNull();
    expect(row.tdcc?.stale).toBe(true);

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
});
