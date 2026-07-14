import { describe, expect, it } from "vitest";

import type { TranslationKey } from "../../app/i18n";
import {
  formatAlertStockResolutionMessage,
  isAlertStockValidationDetail,
  localizeAlertStockApiDetail,
} from "./alertStockValidationCopy";
import { AlertStockResolutionError } from "./resolveAlertStockId";

const enMessages: Partial<Record<TranslationKey, string>> = {
  "alerts.error.unknownStock":
    "Stock not found. Enter a ticker or choose a canonical ID such as US:AAPL.",
  "alerts.error.unknownStockId":
    "Stock not found: {stockId}. Enter a ticker or choose a canonical ID such as US:AAPL.",
  "alerts.error.ambiguousStock": 'Ambiguous symbol "{query}". Choose one of: {choices}',
};

const zhMessages: Partial<Record<TranslationKey, string>> = {
  "alerts.error.unknownStock": "找不到股票。請輸入代號，或使用標準 ID（例如 US:AAPL）。",
  "alerts.error.unknownStockId":
    "找不到股票 {stockId}。請輸入代號，或使用標準 ID（例如 US:AAPL）。",
  "alerts.error.ambiguousStock": "股票代號「{query}」有多筆相符，請選擇：{choices}",
};

function tFactory(messages: Partial<Record<TranslationKey, string>>) {
  return (key: TranslationKey, params?: Record<string, string | number>) => {
    const template = messages[key] ?? key;
    if (!params) {
      return template;
    }
    return Object.entries(params).reduce(
      (result, [paramKey, paramValue]) =>
        result.split(`{${paramKey}}`).join(String(paramValue)),
      template,
    );
  };
}

describe("alertStockValidationCopy", () => {
  it("detects stock-related API validation details", () => {
    expect(isAlertStockValidationDetail("Unknown stock id: US:ZZZZZ")).toBe(true);
    expect(
      isAlertStockValidationDetail(
        'Ambiguous stock symbol "FOO". Choose one of: TW:1111, TW:2222',
      ),
    ).toBe(true);
    expect(isAlertStockValidationDetail("Threshold value must be greater than 0")).toBe(
      false,
    );
  });

  it("localizes unknown and ambiguous resolution errors", () => {
    const t = tFactory(zhMessages);
    expect(
      formatAlertStockResolutionMessage(
        new AlertStockResolutionError("unknown", "US:ZZZZZ"),
        t,
      ),
    ).toBe("找不到股票 US:ZZZZZ。請輸入代號，或使用標準 ID（例如 US:AAPL）。");
    expect(
      formatAlertStockResolutionMessage(
        new AlertStockResolutionError("ambiguous", "FOO", "TW:1111, TW:2222"),
        t,
      ),
    ).toBe("股票代號「FOO」有多筆相符，請選擇：TW:1111, TW:2222");
  });

  it("localizes English backend stock details for the UI language", () => {
    const t = tFactory(enMessages);
    expect(localizeAlertStockApiDetail("Unknown stock id: US:ZZZZZ", t)).toBe(
      "Stock not found: US:ZZZZZ. Enter a ticker or choose a canonical ID such as US:AAPL.",
    );
    expect(
      localizeAlertStockApiDetail(
        'Ambiguous stock symbol "FOO". Choose one of: TW:1111, TW:2222',
        t,
      ),
    ).toBe('Ambiguous symbol "FOO". Choose one of: TW:1111, TW:2222');
  });
});
