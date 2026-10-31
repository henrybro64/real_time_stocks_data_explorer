import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./page";
import { CREATE_ACCOUNT_BUTTON_ENABLED } from "./createAccountUi";
import { loginLocal, registerLocal } from "../../services/api/auth";

const replace = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh, push: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode;
    href: string;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("../../services/api/auth", () => ({
  fetchAuthStatus: vi.fn().mockResolvedValue({
    authEnabled: true,
    loggedIn: false,
    providers: { local: true, entra: true },
    user: null,
  }),
  loginLocal: vi.fn().mockResolvedValue(undefined),
  registerLocal: vi.fn().mockResolvedValue(undefined),
  entraLoginUrl: () => "/api/auth/login/entra",
}));

describe("login page Create account (#304)", () => {
  beforeEach(() => {
    replace.mockReset();
    refresh.mockReset();
    vi.mocked(loginLocal).mockClear();
    vi.mocked(registerLocal).mockClear();
  });

  it("keeps Create account visible but disabled", async () => {
    expect(CREATE_ACCOUNT_BUTTON_ENABLED).toBe(false);
    render(<LoginPage />);
    const create = await screen.findByRole("button", { name: "Create account" });
    expect(create).toBeVisible();
    expect(create).toBeDisabled();
    expect(create).toHaveAttribute("disabled");
    expect(screen.getByText("Sign in with your account.")).toBeVisible();
    expect(screen.queryByText(/Create one in one click/)).not.toBeInTheDocument();
    create.focus();
    expect(document.activeElement).not.toBe(create);

    const user = userEvent.setup();
    await user.click(create);
    await user.keyboard("{Enter}");
    await user.keyboard(" ");
    expect(registerLocal).not.toHaveBeenCalled();
  });

  it("still allows local sign-in and Microsoft sign-in", async () => {
    render(<LoginPage />);
    const signIn = await screen.findByRole("button", { name: "Sign in" });
    expect(signIn).toBeEnabled();
    expect(screen.getByRole("link", { name: "Sign in with Microsoft" })).toHaveAttribute(
      "href",
      "/api/auth/login/entra",
    );

    await userEvent.setup().type(screen.getByLabelText("Username"), "alice");
    await userEvent.setup().type(screen.getByLabelText("Password"), "password-123");
    await userEvent.setup().click(signIn);

    await waitFor(() => {
      expect(loginLocal).toHaveBeenCalledWith("alice", "password-123");
    });
    expect(registerLocal).not.toHaveBeenCalled();
  });
});
