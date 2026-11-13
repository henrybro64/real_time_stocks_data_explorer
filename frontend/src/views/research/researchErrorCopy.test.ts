import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api/client";
import {
  classifyResearchError,
  researchErrorCopy,
  researchErrorRawDetail,
} from "./researchErrorCopy";

function apiError(
  status: number | null,
  detail: string | null,
  requestId?: string,
) {
  return new ApiError(`Request failed: ${status}`, {
    status,
    detail,
    kind: "unknown",
    requestId,
  });
}

const cases: Array<{
  name: string;
  error: unknown;
  kind: string;
  zh: RegExp;
  en: RegExp;
}> = [
  {
    name: "409 first_pass_job_failed",
    error: apiError(
      409,
      JSON.stringify({ error: "first_pass_job_failed", lastError: "boom" }),
    ),
    kind: "job_failed",
    zh: /已停止/,
    en: /stopped/i,
  },
  {
    name: "409 lease held",
    error: apiError(409, JSON.stringify({ error: "lease_held" })),
    kind: "lease_held",
    zh: /正在收集/,
    en: /already being collected/i,
  },
  {
    name: "409 leaseHeld flag",
    error: apiError(409, '{"leaseHeld":true}'),
    kind: "lease_held",
    zh: /正在收集/,
    en: /already being collected/i,
  },
  {
    name: "503 DB unavailable",
    error: apiError(503, JSON.stringify({ error: "durable_store_unavailable" })),
    kind: "db_unavailable",
    zh: /資料庫暫時無法使用/,
    en: /database is temporarily unavailable/i,
  },
  {
    name: "504 gateway timeout",
    error: apiError(504, null),
    kind: "timeout",
    zh: /逾時/,
    en: /timed out/i,
  },
  {
    name: "network failure",
    error: new TypeError("Failed to fetch"),
    kind: "timeout",
    zh: /網路/,
    en: /network/i,
  },
  {
    name: "429",
    error: apiError(429, "rate limited"),
    kind: "rate_limited",
    zh: /過於頻繁/,
    en: /too many requests/i,
  },
  {
    name: "generic 500",
    error: apiError(500, "internal"),
    kind: "generic",
    zh: /未預期/,
    en: /something went wrong/i,
  },
  {
    name: "unknown 409",
    error: apiError(409, "conflict"),
    kind: "generic",
    zh: /未預期/,
    en: /something went wrong/i,
  },
];

describe("researchErrorCopy", () => {
  for (const item of cases) {
    it(`${item.name} → ${item.kind} (zh-TW and en)`, () => {
      expect(classifyResearchError(item.error)).toBe(item.kind);
      const zh = researchErrorCopy(item.error, "zh-TW");
      const en = researchErrorCopy(item.error, "en");
      expect(zh.kind).toBe(item.kind);
      expect(zh.title).toMatch(item.zh);
      expect(en.title).toMatch(item.en);
      expect(zh.nextAction.length).toBeGreaterThan(0);
      expect(en.nextAction.length).toBeGreaterThan(0);
      // Plain copy never leaks raw backend tokens.
      for (const text of [zh.title, zh.nextAction, en.title, en.nextAction]) {
        expect(text).not.toMatch(/first_pass_job_failed|leaseHeld|lease_held|durable_store/);
      }
    });
  }

  it("keeps raw detail and request id for the advanced section", () => {
    const raw = researchErrorRawDetail(apiError(503, "durable_store_unavailable", "req-9"));
    expect(raw).toContain("durable_store_unavailable");
    expect(raw).toContain("request req-9");
  });
});
