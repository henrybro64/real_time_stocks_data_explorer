"use client";

import Link from "next/link";
import { useLanguage } from "../../app/i18n";
import { toApiError } from "../../services/api/client";

export type RequestNoticeKind =
  | "auth"
  | "permission"
  | "unavailable"
  | "validation"
  | "error"
  | "stale";

type Props = {
  className?: string;
  compact?: boolean;
  detail?: string | null;
  kind: RequestNoticeKind;
  scopeLabel: string;
};

export function errorToNoticeKind(error: unknown): RequestNoticeKind {
  const apiError = toApiError(error);
  switch (apiError.kind) {
    case "auth":
      return "auth";
    case "permission":
      return "permission";
    case "unavailable":
      return "unavailable";
    case "validation":
      return "validation";
    default:
      return "error";
  }
}

export function RequestStateNotice({
  className = "",
  compact = false,
  detail = null,
  kind,
  scopeLabel,
}: Props) {
  const { language } = useLanguage();
  const scope = language === "en" ? scopeLabel.toLowerCase() : scopeLabel;
  const detailText = detail?.trim() ? detail.trim() : null;

  const copy =
    language === "zh-TW"
      ? {
          auth: {
            title: "需要登入",
            body: `請先登入，才能讀取${scope}。`,
            action: "前往登入",
          },
          permission: {
            title: "沒有權限",
            body: `您目前沒有權限存取${scope}。`,
            action: null,
          },
          unavailable: {
            title: "資料來源暫時不可用",
            body: `${scope}目前無法從資料提供方取得，請稍後再試。`,
            action: null,
          },
          validation: {
            title: "無法儲存",
            body: detailText ?? `${scope}輸入有誤，請修正後再試。`,
            action: null,
          },
          error: {
            title: "載入失敗",
            body: `${scope}發生載入問題，請稍後重試。`,
            action: null,
          },
          stale: {
            title: "顯示較舊資料",
            body: `最新重新整理失敗，目前顯示的是先前已載入的${scope}。`,
            action: null,
          },
        }
      : {
          auth: {
            title: "Sign-in required",
            body: `Sign in to load ${scope}.`,
            action: "Sign in",
          },
          permission: {
            title: "Permission denied",
            body: `You do not have permission to access ${scope}.`,
            action: null,
          },
          unavailable: {
            title: "Provider unavailable",
            body: `The data provider for ${scope} is temporarily unavailable.`,
            action: null,
          },
          validation: {
            title: "Could not save",
            body: detailText ?? `Check the ${scope} form and try again.`,
            action: null,
          },
          error: {
            title: "Load failed",
            body: `There was a problem loading ${scope}.`,
            action: null,
          },
          stale: {
            title: "Showing older data",
            body: `The latest refresh failed, so you're seeing the most recent ${scope} already loaded.`,
            action: null,
          },
        };

  const current = copy[kind];

  return (
    <div
      className={[
        "app-state-notice",
        `app-state-notice-${kind}`,
        compact ? "app-state-notice-compact" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      role={kind === "stale" ? "status" : "alert"}
    >
      <p className="app-state-notice-title">{current.title}</p>
      <p className="app-state-notice-body">{current.body}</p>
      {kind === "auth" && current.action ? (
        <Link className="app-state-notice-action" href="/login">
          {current.action}
        </Link>
      ) : null}
    </div>
  );
}
