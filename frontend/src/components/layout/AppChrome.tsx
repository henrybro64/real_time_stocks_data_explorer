"use client";

import { type ReactNode, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Shell } from "./Shell";
import { fetchAuthStatus } from "../../services/api/auth";

type Props = {
  children: ReactNode;
};

type GateState = "loading" | "allowed" | "redirecting";

/**
 * When auth is enabled on the API, unauthenticated users are sent to /login before any shell UI.
 */
export function AppChrome({ children }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const [gate, setGate] = useState<GateState>("loading");

  useEffect(() => {
    if (pathname === "/login") {
      setGate("allowed");
      return;
    }

    let cancelled = false;
    setGate("loading");

    fetchAuthStatus()
      .then((s) => {
        if (cancelled) {
          return;
        }
        if (s.authEnabled && !s.loggedIn) {
          setGate("redirecting");
          router.replace("/login");
          return;
        }
        setGate("allowed");
      })
      .catch(() => {
        if (!cancelled) {
          // API unreachable: show app (fail open) so local dev without backend still works
          setGate("allowed");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (pathname === "/login") {
    return <>{children}</>;
  }

  if (gate === "loading" || gate === "redirecting") {
    return (
      <div className="app-auth-gate">
        <p className="cds--body-compact-01">Loading…</p>
      </div>
    );
  }

  return <Shell>{children}</Shell>;
}
