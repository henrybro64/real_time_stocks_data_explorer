"use client";

import { Button } from "@carbon/react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Shell } from "./Shell";
import { useLanguage } from "../../app/i18n";
import { fetchAuthStatus } from "../../services/api/auth";
import { ApiError, toApiError } from "../../services/api/client";

type Props = {
  children: ReactNode;
};

type GateState = "loading" | "allowed" | "redirecting" | "unreachable";

const OFFLINE_SHELL =
  typeof process !== "undefined" && process.env.NEXT_PUBLIC_ALLOW_OFFLINE_SHELL === "true";

function isBackendUnreachable(error: unknown): boolean {
  const apiError = error instanceof ApiError ? error : toApiError(error);
  if (apiError.status == null) {
    return true;
  }
  return apiError.status >= 500;
}

/**
 * When auth is enabled on the API, unauthenticated users are sent to /login before any shell UI.
 * Backend outages show an explicit unreachable state instead of a blank dashboard (#285).
 */
export function AppChrome({ children }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useLanguage();
  const [gate, setGate] = useState<GateState>("loading");
  const [retryTick, setRetryTick] = useState(0);
  const attemptRef = useRef(0);

  const retryNow = useCallback(() => {
    attemptRef.current = 0;
    setRetryTick((value) => value + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    setGate("loading");

    const probe = () => {
      fetchAuthStatus()
        .then((s) => {
          if (cancelled) {
            return;
          }
          attemptRef.current = 0;
          if (pathname === "/login") {
            setGate("allowed");
            return;
          }
          if (s.authEnabled && !s.loggedIn) {
            setGate("redirecting");
            router.replace("/login");
            return;
          }
          setGate("allowed");
        })
        .catch((error: unknown) => {
          if (cancelled) {
            return;
          }
          if (OFFLINE_SHELL) {
            setGate("allowed");
            return;
          }
          if (!isBackendUnreachable(error)) {
            // Unexpected client/auth response: still avoid a false dashboard shell.
            setGate("unreachable");
            return;
          }
          setGate("unreachable");
          const attempt = attemptRef.current;
          attemptRef.current = attempt + 1;
          const delayMs = Math.min(30_000, 1_000 * 2 ** Math.min(attempt, 4));
          retryTimer = setTimeout(() => {
            if (!cancelled) {
              setGate("loading");
              probe();
            }
          }, delayMs);
        });
    };

    probe();

    return () => {
      cancelled = true;
      if (retryTimer) {
        clearTimeout(retryTimer);
      }
    };
  }, [pathname, router, retryTick]);

  if (gate === "unreachable") {
    return (
      <div className="app-auth-gate app-auth-gate-unreachable" role="alert">
        <p className="cds--heading-03">{t("auth.serverUnreachableTitle")}</p>
        <p className="cds--body-compact-01">{t("auth.serverUnreachableBody")}</p>
        <Button kind="primary" size="md" onClick={retryNow}>
          {t("auth.retry")}
        </Button>
      </div>
    );
  }

  if (gate === "loading" || gate === "redirecting") {
    return (
      <div className="app-auth-gate">
        <p className="cds--body-compact-01">{t("auth.loading")}</p>
      </div>
    );
  }

  if (pathname === "/login") {
    return <>{children}</>;
  }

  return <Shell>{children}</Shell>;
}
