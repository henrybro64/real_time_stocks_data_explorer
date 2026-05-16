import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api/client";
import {
  classifyPortfolioAnalysisError,
  PORTFOLIO_ANALYSIS_AI_TEMPORARILY_UNAVAILABLE_WARNING,
  PORTFOLIO_ANALYSIS_BACKEND_ERRORS,
} from "./portfolioAnalysisErrors";

describe("portfolioAnalysisErrors", () => {
  it("matches backend AI_TEMPORARILY_UNAVAILABLE wording so Portfolio Page styling stays aligned", () => {
    expect(PORTFOLIO_ANALYSIS_AI_TEMPORARILY_UNAVAILABLE_WARNING).toBe(
      "AI analysis is temporarily unavailable, but computed portfolio metrics are still available.",
    );
  });
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

  it("classifies no-analyzable and empty-portfolio backend errors", () => {
    expect(
      classifyPortfolioAnalysisError(
        new ApiError("Request failed: 400", {
          status: 400,
          detail: PORTFOLIO_ANALYSIS_BACKEND_ERRORS.NO_ANALYZABLE_HOLDINGS,
          kind: "validation",
        }),
      ),
    ).toBe("no_analyzable_holdings");

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
