"use client";

import {
  Button,
  Column,
  Grid,
  Heading,
  Stack,
  Theme,
  Tile,
} from "@carbon/react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { fetchAuthStatus, loginUrl } from "../../services/api/auth";

function LoginFallback() {
  return (
    <Theme theme="g100" as="div" className="app-login-root">
      <div className="app-login-backdrop" />
    </Theme>
  );
}

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const error = searchParams?.get("error");
  const [ready, setReady] = useState(false);
  const [allowed, setAllowed] = useState(false);
  const [providers, setProviders] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await fetchAuthStatus();
        if (cancelled) {
          return;
        }
        if (!s.authEnabled) {
          router.replace("/");
          return;
        }
        if (s.loggedIn) {
          router.replace("/");
          return;
        }
        setAllowed(true);
        setProviders(s.providers);
      } catch {
        if (!cancelled) {
          setAllowed(true);
        }
      } finally {
        if (!cancelled) {
          setReady(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!ready) {
    return <LoginFallback />;
  }

  if (!allowed) {
    return null;
  }

  return (
    <Theme theme="g100" as="div" className="app-login-root">
      <div className="app-login-backdrop" aria-hidden />
      <Grid className="app-login-grid">
        <Column lg={6} md={4} sm={4}>
          <Tile className="app-login-tile">
            <Stack gap={6}>
              <div>
                <p className="cds--label-01 app-login-eyebrow">Stock Ticker</p>
                <Heading className="app-login-title">Sign in</Heading>
                <p className="cds--body-compact-01 app-login-subtitle">
                  Use a connected account. Your credentials stay with the provider; we only store
                  your session after you approve access.
                </p>
              </div>
              {error ? (
                <p className="cds--label-01 app-login-error" role="alert">
                  Sign-in did not complete. Try again or pick another provider.
                </p>
              ) : null}
              <Stack gap={3}>
                {providers.google ? (
                  <Button
                    kind="tertiary"
                    size="lg"
                    onClick={() => {
                      window.location.href = loginUrl("google");
                    }}
                  >
                    Continue with Google
                  </Button>
                ) : null}
                {providers.microsoft ? (
                  <Button
                    kind="tertiary"
                    size="lg"
                    onClick={() => {
                      window.location.href = loginUrl("microsoft");
                    }}
                  >
                    Continue with Microsoft
                  </Button>
                ) : null}
                {providers.apple ? (
                  <Button
                    kind="tertiary"
                    size="lg"
                    onClick={() => {
                      window.location.href = loginUrl("apple");
                    }}
                  >
                    Continue with Apple
                  </Button>
                ) : null}
                {!providers.google && !providers.microsoft && !providers.apple ? (
                  <p className="cds--helper-text-01">
                    No OAuth providers are configured on the server. Set Google, Microsoft, and/or
                    Apple client IDs in the backend environment.
                  </p>
                ) : null}
              </Stack>
              <Link className="cds--link app-login-back" href="/">
                Back to dashboard
              </Link>
            </Stack>
          </Tile>
        </Column>
      </Grid>
    </Theme>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<LoginFallback />}>
      <LoginPageInner />
    </Suspense>
  );
}
