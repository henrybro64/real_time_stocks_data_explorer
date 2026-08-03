import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "../../app/i18n";
import { QuoteFreshnessTag } from "./QuoteFreshnessTag";

function renderTag(props: React.ComponentProps<typeof QuoteFreshnessTag>) {
  return render(
    <LanguageProvider>
      <QuoteFreshnessTag {...props} />
    </LanguageProvider>,
  );
}

describe("QuoteFreshnessTag", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-21T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows live for recent open-market quotes", () => {
    renderTag({
      isMarketOpen: true,
      timestamp: "2026-04-21T11:59:00Z",
    });

    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("即時");
  });

  it("promotes failed refreshes to stale even when the cached timestamp is recent", () => {
    renderTag({
      isMarketOpen: true,
      refreshFailed: true,
      timestamp: "2026-04-21T11:57:00Z",
    });

    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("過舊");
  });

  it("shows updating from API freshness metadata", () => {
    renderTag({
      freshnessMeta: {
        cachedAt: null,
        ageSeconds: null,
        stale: true,
        source: "none",
        refreshStatus: "queued",
      },
      hasQuote: false,
      isMarketOpen: false,
      timestamp: "",
    });

    expect(screen.getByTestId("quote-freshness-tag")).toHaveTextContent("更新中");
  });
});
