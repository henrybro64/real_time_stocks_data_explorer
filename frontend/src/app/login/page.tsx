"use client";

import {
  Button,
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
import { entraLoginUrl, fetchAuthStatus, loginLocal, registerLocal } from "../../services/api/auth";
import { toApiError } from "../../services/api/client";

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
    void (async () => {
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
      <div className="app-login-grid">
        <Tile className="app-login-tile">
          <Stack gap={6}>
            <div>
              <p className="cds--label-01 app-login-eyebrow">Stock Ticker</p>
              <Heading className="app-login-title">Sign in</Heading>
              <p className="cds--body-compact-01 app-login-subtitle">
                Sign in with your account. New here? Create one in one click.
              </p>
            </div>
            {localError ? (
              <p className="cds--label-01 app-login-error" role="alert">
                {localError}
              </p>
            ) : null}
            <Stack gap={3}>
              {providers.entra ? (
                <Button
                  size="lg"
                  kind={providers.local ? "tertiary" : "primary"}
                  href={entraLoginUrl()}
                >
                  Sign in with Microsoft
                </Button>
              ) : null}
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
                      } catch (error) {
                        const apiError = toApiError(error);
                        setLocalError(
                          apiError.status === 429 && apiError.detail
                            ? apiError.detail
                            : "Invalid username or password.",
                        );
                      }
                    }}
                  >
                    Sign in
                  </Button>
                  <Button
                    kind="tertiary"
                    size="lg"
                    disabled
                    onClick={async () => {
                      setLocalError(null);
                      const uname = localUsername.trim();
                      if (!uname || localPassword.length < 8) {
                        setLocalError("Use a username and password with at least 8 characters.");
                        return;
                      }
                      try {
                        await registerLocal(uname, localPassword);
                        router.replace("/");
                        router.refresh();
                      } catch (error) {
                        const apiError = toApiError(error);
                        setLocalError(
                          apiError.status === 429 && apiError.detail
                            ? apiError.detail
                            : "Could not create account (username may already exist).",
                        );
                      }
                    }}
                  >
                    Create account
                  </Button>
                </Stack>
              ) : null}
              {!providers.local && !providers.entra ? (
                <p className="cds--helper-text-01">
                  No login providers are configured. Enable local auth and/or Entra External ID
                  on the backend.
                </p>
              ) : null}
            </Stack>
            <Link className="cds--link app-login-back" href="/">
              Back to dashboard
            </Link>
          </Stack>
        </Tile>
      </div>
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
