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

type Props = Readonly<{
  className?: string;
  compact?: boolean;
  detail?: string | null;
  kind: RequestNoticeKind;
  scopeLabel: string;
  status?: number | null;
}>;

type NoticeCopy = Readonly<{
  title: string;
  body: string;
  action: string | null;
}>;

type NoticeCopyMap = Readonly<Record<RequestNoticeKind, NoticeCopy>>;

type ProviderFlags = Readonly<{
  schemaFailure: boolean;
  rateLimited: boolean;
  accessDenied: boolean;
  timedOut: boolean;
}>;

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

function providerFlagsFromDetail(detailLower: string, status: number | null): ProviderFlags {
  return {
    schemaFailure: detailLower.includes("could not be interpreted"),
    rateLimited: detailLower.includes("rate-limited") || status === 429,
    accessDenied: detailLower.includes("denied access"),
    timedOut:
      detailLower.includes("timeout") ||
      detailLower.includes("timed out") ||
      status === 504,
  };
}

function noticeDiagnostic(status: number | null, detailText: string | null): string | null {
  const parts = [status != null ? String(status) : null, detailText].filter(
    (part): part is string => Boolean(part),
  );
  return parts.length > 0 ? parts.join(": ") : null;
}

function errorNotice(
  scope: string,
  diagnostic: string | null,
  language: "en" | "zh-TW",
): NoticeCopy {
  if (language === "zh-TW") {
    return {
      title: "載入失敗",
      body: diagnostic
        ? `${scope}發生載入問題，請稍後重試。（${diagnostic}）`
        : `${scope}發生載入問題，請稍後重試。`,
      action: null,
    };
  }
  return {
    title: "Load failed",
    body: diagnostic
      ? `There was a problem loading ${scope}. (${diagnostic})`
      : `There was a problem loading ${scope}.`,
    action: null,
  };
}

function unavailableNotice(
  scope: string,
  flags: ProviderFlags,
  language: "en" | "zh-TW",
): NoticeCopy {
  if (language === "zh-TW") {
    if (flags.schemaFailure) {
      return {
        title: "官方資料格式無法解析",
        body: `${scope}已連上官方來源，但回應格式無法判讀，請稍後再試。`,
        action: null,
      };
    }
    if (flags.rateLimited) {
      return {
        title: "官方資料來源流量受限",
        body: `${scope}的官方資料來源目前受到流量限制，請稍後再試。`,
        action: null,
      };
    }
    if (flags.timedOut) {
      return {
        title: "官方資料來源逾時",
        body: `${scope}連線官方資料來源逾時，請稍後再試。`,
        action: null,
      };
    }
    return {
      title: "資料來源暫時不可用",
      body: `${scope}目前無法從資料提供方取得，請稍後再試。`,
      action: null,
    };
  }
  if (flags.schemaFailure) {
    return {
      title: "Official response could not be interpreted",
      body: `${scope} reached the official source, but the response shape could not be interpreted.`,
      action: null,
    };
  }
  if (flags.rateLimited) {
    return {
      title: "Official source rate-limited",
      body: `The official data source for ${scope} is rate-limited right now.`,
      action: null,
    };
  }
  if (flags.timedOut) {
    return {
      title: "Official source timed out",
      body: `The official data source for ${scope} timed out.`,
      action: null,
    };
  }
  return {
    title: "Provider unavailable",
    body: `The data provider for ${scope} is temporarily unavailable.`,
    action: null,
  };
}

function permissionNotice(
  scope: string,
  flags: ProviderFlags,
  language: "en" | "zh-TW",
): NoticeCopy {
  if (language === "zh-TW") {
    if (flags.accessDenied) {
      return {
        title: "官方資料來源拒絕存取",
        body: `${scope}的官方資料來源回傳了拒絕存取的回應。這不是本站登入問題。`,
        action: null,
      };
    }
    return {
      title: "沒有權限",
      body: `您目前沒有權限存取${scope}。`,
      action: null,
    };
  }
  if (flags.accessDenied) {
    return {
      title: "Official source denied access",
      body: `The official data source for ${scope} returned an access-denied response. This is not a site sign-in problem.`,
      action: null,
    };
  }
  return {
    title: "Permission denied",
    body: `You do not have permission to access ${scope}.`,
    action: null,
  };
}

function buildNoticeCopy(
  language: string,
  scope: string,
  detailText: string | null,
  flags: ProviderFlags,
  status: number | null,
): NoticeCopyMap {
  const locale = language === "zh-TW" ? "zh-TW" : "en";
  const diagnostic = noticeDiagnostic(status, detailText);
  if (locale === "zh-TW") {
    return {
      auth: {
        title: "需要登入",
        body: `請先登入，才能讀取${scope}。`,
        action: "前往登入",
      },
      permission: permissionNotice(scope, flags, locale),
      unavailable: unavailableNotice(scope, flags, locale),
      validation: {
        title: "無法儲存",
        body: detailText ?? `${scope}輸入有誤，請修正後再試。`,
        action: null,
      },
      error: errorNotice(scope, diagnostic, locale),
      stale: {
        title: "顯示較舊資料",
        body: `最新重新整理失敗，目前顯示的是先前已載入的${scope}。`,
        action: null,
      },
    };
  }
  return {
    auth: {
      title: "Sign-in required",
      body: `Sign in to load ${scope}.`,
      action: "Sign in",
    },
    permission: permissionNotice(scope, flags, locale),
    unavailable: unavailableNotice(scope, flags, locale),
    validation: {
      title: "Could not save",
      body: detailText ?? `Check the ${scope} form and try again.`,
      action: null,
    },
    error: errorNotice(scope, diagnostic, locale),
    stale: {
      title: "Showing older data",
      body: `The latest refresh failed, so you're seeing the most recent ${scope} already loaded.`,
      action: null,
    },
  };
}

export function RequestStateNotice({
  className = "",
  compact = false,
  detail = null,
  kind,
  scopeLabel,
  status = null,
}: Props) {
  const { language } = useLanguage();
  const scope = language === "en" ? scopeLabel.toLowerCase() : scopeLabel;
  const detailText = detail?.trim() ? detail.trim() : null;
  const flags = providerFlagsFromDetail(detailText?.toLowerCase() ?? "", status);
  const current = buildNoticeCopy(language, scope, detailText, flags, status)[kind];

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
