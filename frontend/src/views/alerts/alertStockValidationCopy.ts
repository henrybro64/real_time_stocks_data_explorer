import type { TranslationKey } from "../../app/i18n";
import {
  AlertStockResolutionError,
  type AlertStockResolutionCode,
} from "./resolveAlertStockId";

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string;

const AMBIGUOUS_DETAIL =
  /^Ambiguous stock symbol "([^"]+)"\. Choose one of: (.+)$/;
const UNKNOWN_DETAIL = /^Unknown stock id: (.*)$/;
const UNKNOWN_DETAIL_EMPTY = /^Unknown stock id:$/;

/** True when an API/validation detail string is stock-id related (show as field error). */
export function isAlertStockValidationDetail(detail: string | null | undefined): boolean {
  if (!detail?.trim()) {
    return false;
  }
  const text = detail.trim();
  return (
    AMBIGUOUS_DETAIL.test(text) ||
    UNKNOWN_DETAIL.test(text) ||
    UNKNOWN_DETAIL_EMPTY.test(text)
  );
}

export function formatAlertStockResolutionMessage(
  error: AlertStockResolutionError,
  t: Translate,
): string {
  return formatStockValidationMessage(error.code, error.query, error.choices, t);
}

/** Map backend English stock-validation details to localized UI copy. */
export function localizeAlertStockApiDetail(detail: string, t: Translate): string {
  const text = detail.trim();
  const ambiguous = AMBIGUOUS_DETAIL.exec(text);
  if (ambiguous) {
    return formatStockValidationMessage("ambiguous", ambiguous[1], ambiguous[2], t);
  }
  const unknown = UNKNOWN_DETAIL.exec(text);
  if (unknown) {
    return formatStockValidationMessage("unknown", unknown[1] ?? "", null, t);
  }
  if (UNKNOWN_DETAIL_EMPTY.test(text)) {
    return formatStockValidationMessage("unknown", "", null, t);
  }
  return text;
}

function formatStockValidationMessage(
  code: AlertStockResolutionCode,
  query: string,
  choices: string | null,
  t: Translate,
): string {
  if (code === "ambiguous") {
    return t("alerts.error.ambiguousStock", {
      query,
      choices: choices ?? "",
    });
  }
  if (query.trim()) {
    return t("alerts.error.unknownStockId", { stockId: query.trim() });
  }
  return t("alerts.error.unknownStock");
}
