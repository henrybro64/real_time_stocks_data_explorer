import { requestJson, requestVoid } from "./client";

export type AuthStatus = {
  authEnabled: boolean;
  loggedIn: boolean;
  providers: Record<string, boolean>;
  user: { id: string; username: string } | null;
};

export async function fetchAuthStatus(): Promise<AuthStatus> {
  return requestJson<AuthStatus>("/auth/status");
}

export async function logout(): Promise<void> {
  await requestVoid("/auth/logout", {
    method: "POST",
  });
}

export async function loginLocal(username: string, password: string): Promise<void> {
  await requestVoid("/auth/login/local", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({ username, password }),
  });
}

export async function registerLocal(username: string, password: string): Promise<void> {
  await requestVoid("/auth/register/local", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({ username, password }),
  });
}
