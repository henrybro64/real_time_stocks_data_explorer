/** Plain-language error copy for TW research requests (#350/#351). */

import { toApiError } from "../../services/api/client";

export type ResearchErrorKind =
  | "job_failed"
  | "lease_held"
  | "db_unavailable"
  | "timeout"
  | "rate_limited"
  | "generic";

export type ResearchErrorCopy = {
  kind: ResearchErrorKind;
  title: string;
  nextAction: string;
};

type Language = "zh-TW" | "en";

const COPY: Record<
  ResearchErrorKind,
  Record<Language, { title: string; nextAction: string }>
> = {
  job_failed: {
    "zh-TW": {
      title: "這次研究的資料收集已停止",
      nextAction: "請展開「進階資訊」查看原因，修復後再重新開始研究。",
    },
    en: {
      title: "Data collection for this research has stopped",
      nextAction:
        "Open Advanced details to see the cause, then start research again once it is fixed.",
    },
  },
  lease_held: {
    "zh-TW": {
      title: "已有一批資料正在收集中",
      nextAction: "請稍候幾分鐘再試，不需要重複送出。",
    },
    en: {
      title: "A batch is already being collected",
      nextAction: "Wait a few minutes and try again. There is no need to submit twice.",
    },
  },
  db_unavailable: {
    "zh-TW": {
      title: "資料庫暫時無法使用",
      nextAction: "請稍後再試。畫面上顯示的可能是較舊的資料。",
    },
    en: {
      title: "The database is temporarily unavailable",
      nextAction: "Try again in a little while. What you see may be older data.",
    },
  },
  timeout: {
    "zh-TW": {
      title: "連線逾時或網路中斷",
      nextAction: "請檢查網路連線後再試一次。",
    },
    en: {
      title: "The request timed out or the network dropped",
      nextAction: "Check your connection and try again.",
    },
  },
  rate_limited: {
    "zh-TW": {
      title: "請求過於頻繁",
      nextAction: "請等待約一分鐘後再試。",
    },
    en: {
      title: "Too many requests",
      nextAction: "Wait about a minute, then try again.",
    },
  },
  generic: {
    "zh-TW": {
      title: "發生未預期的錯誤",
      nextAction: "請稍後再試；若持續發生，請展開「進階資訊」查看詳細內容。",
    },
    en: {
      title: "Something went wrong",
      nextAction:
        "Try again later. If it keeps happening, open Advanced details for the full message.",
    },
  },
};

function detailMentions(detail: string | null, ...needles: string[]): boolean {
  if (!detail) return false;
  const lower = detail.toLowerCase();
  return needles.some((needle) => lower.includes(needle));
}

export function classifyResearchError(error: unknown): ResearchErrorKind {
  const api = toApiError(error);
  const status = api.status;
  const detail = api.detail;
  if (status === 409) {
    if (detailMentions(detail, "first_pass_job_failed")) return "job_failed";
    if (detailMentions(detail, "leaseheld", "lease_held", "lease held")) {
      return "lease_held";
    }
    return "generic";
  }
  if (status === 429) return "rate_limited";
  if (status === 503) return "db_unavailable";
  if (status === 504 || status === 408) return "timeout";
  if (status == null) {
    const text = `${detail ?? ""} ${api.message}`;
    if (detailMentions(text, "timeout", "timed out", "network", "failed to fetch", "abort")) {
      return "timeout";
    }
  } else if (detailMentions(detail, "timeout", "timed out")) {
    return "timeout";
  }
  return "generic";
}

export function researchErrorCopy(
  error: unknown,
  language: string,
): ResearchErrorCopy {
  const kind = classifyResearchError(error);
  const locale: Language = language === "en" ? "en" : "zh-TW";
  return { kind, ...COPY[kind][locale] };
}

/** Raw detail + request id, intended for the Advanced details disclosure only. */
export function researchErrorRawDetail(error: unknown): string {
  const api = toApiError(error);
  const text = api.detail || api.message;
  return api.requestId ? `${text} · request ${api.requestId}` : text;
}
