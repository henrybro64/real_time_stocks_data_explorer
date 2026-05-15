import { ApiError } from "../../services/api/client";

/** Must stay aligned with `backend/app/services/portfolio_analysis_service.py`. */
export const PORTFOLIO_ANALYSIS_BACKEND_ERRORS = {
  AI_NOT_CONFIGURED:
    "AI is not configured. Please set up your AI provider in AI Settings.",
  EMPTY_PORTFOLIO: "Portfolio is empty.",
  NO_ANALYZABLE_HOLDINGS:
    "Portfolio analysis requires at least one holding with an available quote.",
} as const;

export type PortfolioAnalysisErrorKind =
  | "not_configured"
  | "empty_portfolio"
  | "no_analyzable_holdings"
  | "generic";

export function getPortfolioAnalysisErrorDetail(error: unknown): string | null {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    const marker = " - ";
    const markerIndex = error.message.indexOf(marker);
    if (markerIndex >= 0) {
      return error.message.slice(markerIndex + marker.length);
    }
    return error.message;
  }
  return null;
}

export function classifyPortfolioAnalysisError(error: unknown): PortfolioAnalysisErrorKind | null {
  if (error == null) {
    return null;
  }

  const detail = getPortfolioAnalysisErrorDetail(error);
  if (detail === PORTFOLIO_ANALYSIS_BACKEND_ERRORS.AI_NOT_CONFIGURED) {
    return "not_configured";
  }
  if (detail === PORTFOLIO_ANALYSIS_BACKEND_ERRORS.EMPTY_PORTFOLIO) {
    return "empty_portfolio";
  }
  if (detail === PORTFOLIO_ANALYSIS_BACKEND_ERRORS.NO_ANALYZABLE_HOLDINGS) {
    return "no_analyzable_holdings";
  }
  return "generic";
}
