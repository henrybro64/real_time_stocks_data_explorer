import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api/client";
import {
  classifyPortfolioAnalysisError,
  PORTFOLIO_ANALYSIS_BACKEND_ERRORS,
} from "./portfolioAnalysisErrors";

describe("portfolioAnalysisErrors", () => {
  it("classifies ApiError details from the portfolio analysis endpoint", () => {
    const error = new ApiError(
      `Request failed: 400 - ${PORTFOLIO_ANALYSIS_BACKEND_ERRORS.AI_NOT_CONFIGURED}`,
      {
        status: 400,
        detail: PORTFOLIO_ANALYSIS_BACKEND_ERRORS.AI_NOT_CONFIGURED,
        kind: "validation",
      },
    );

    expect(classifyPortfolioAnalysisError(error)).toBe("not_configured");
  });

  it("classifies no-quotable and empty-portfolio backend errors", () => {
    expect(
      classifyPortfolioAnalysisError(
        new ApiError("Request failed: 400", {
          status: 400,
          detail: PORTFOLIO_ANALYSIS_BACKEND_ERRORS.NO_QUOTABLE_HOLDINGS,
          kind: "validation",
        }),
      ),
    ).toBe("no_quotable_holdings");

    expect(
      classifyPortfolioAnalysisError(
        new ApiError("Request failed: 400", {
          status: 400,
          detail: PORTFOLIO_ANALYSIS_BACKEND_ERRORS.EMPTY_PORTFOLIO,
          kind: "validation",
        }),
      ),
    ).toBe("empty_portfolio");
  });
});
