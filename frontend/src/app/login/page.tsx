"use client";

import {
  Button,
  Column,
  Grid,
  Heading,
  PasswordInput,
  Stack,
  TextInput,
  Theme,
  Tile,
} from "@carbon/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { fetchAuthStatus, loginLocal } from "../../services/api/auth";

function LoginFallback() {
  return (
    <Theme theme="g100" as="div" className="app-login-root">
      <div className="app-login-backdrop" />
    </Theme>
  );
}

function LoginPageInner() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [allowed, setAllowed] = useState(false);
  const [providers, setProviders] = useState<Record<string, boolean>>({});
  const [localUsername, setLocalUsername] = useState("");
  const [localPassword, setLocalPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

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
                  Sign in with your local account. We only store your session.
                </p>
              </div>
              {localError ? (
                <p className="cds--label-01 app-login-error" role="alert">
                  {localError}
                </p>
              ) : null}
              <Stack gap={3}>
                {providers.local ? (
                  <Stack gap={4}>
                    <TextInput
                      id="local-username"
                      labelText="Username"
                      value={localUsername}
                      onChange={(e) => setLocalUsername(e.target.value)}
                      autoComplete="username"
                    />
                    <PasswordInput
                      id="local-password"
                      labelText="Password"
                      value={localPassword}
                      onChange={(e) => setLocalPassword(e.target.value)}
                      autoComplete="current-password"
                    />
                    <Button
                      size="lg"
                      onClick={async () => {
                        setLocalError(null);
                        try {
                          await loginLocal(localUsername.trim(), localPassword);
                          router.replace("/");
                          router.refresh();
                        } catch {
                          setLocalError("Invalid username or password.");
                        }
                      }}
                    >
                      Continue with local account
                    </Button>
                  </Stack>
                ) : null}
                {!providers.local ? (
                  <p className="cds--helper-text-01">
                    Local auth is not configured on the server. Set LOCAL_AUTH_ENABLED,
                    LOCAL_AUTH_USERNAME and LOCAL_AUTH_PASSWORD in backend environment.
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
