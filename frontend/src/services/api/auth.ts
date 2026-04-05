import { apiUrl } from "./client";

export type AuthStatus = {
  authEnabled: boolean;
  loggedIn: boolean;
  providers: Record<string, boolean>;
  user: { id: string; username: string } | null;
};

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const response = await fetch(apiUrl("/auth/status"), {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`Auth status failed: ${response.status}`);
  }
  return (await response.json()) as AuthStatus;
}

export async function logout(): Promise<void> {
  const response = await fetch(apiUrl("/auth/logout"), {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`Logout failed: ${response.status}`);
  }
}

export function loginUrl(provider: "google" | "microsoft" | "apple"): string {
  return apiUrl(`/auth/login/${provider}`);
}
