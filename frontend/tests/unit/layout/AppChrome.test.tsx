import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../../../src/app/i18n";
import { AppChrome } from "../../../src/components/layout/AppChrome";
import { fetchAuthStatus } from "../../../src/services/api/auth";
import { ApiError } from "../../../src/services/api/client";

const replace = vi.fn();
// Next returns a stable router; a fresh object per render would re-run the gate effect forever.
const router = { replace, push: vi.fn() };
let pathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => router,
}));

vi.mock("../../../src/services/api/auth", () => ({
  fetchAuthStatus: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("../../../src/components/layout/Shell", () => ({
  Shell: ({ children }: { children: React.ReactNode }) => <div data-testid="shell">{children}</div>,
}));

function renderChrome() {
  return render(
    <LanguageProvider>
      <AppChrome>
        <div>content</div>
      </AppChrome>
    </LanguageProvider>,
  );
}

function httpError(status: number) {
  return new ApiError(`HTTP ${status}`, { status, kind: status >= 500 ? "unavailable" : "unknown" });
}

describe("AppChrome auth gate (#285)", () => {
  beforeEach(() => {
    pathname = "/";
    replace.mockReset();
    vi.mocked(fetchAuthStatus).mockReset();
    window.localStorage.clear();
    window.localStorage.setItem("stock-ticker-language", "en");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the unreachable screen on a network failure", async () => {
    vi.mocked(fetchAuthStatus).mockRejectedValue(new TypeError("Failed to fetch"));
    renderChrome();

    expect(await screen.findByText("Cannot reach the server")).toBeInTheDocument();
    expect(screen.queryByTestId("shell")).not.toBeInTheDocument();
  });

  it("sends a 401 from the status probe to sign-in instead of an outage screen", async () => {
    vi.mocked(fetchAuthStatus).mockRejectedValue(httpError(401));
    renderChrome();

    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("Cannot reach the server")).not.toBeInTheDocument();
  });

  it("names an unexpected 4xx as a server error, not an unreachable server", async () => {
    vi.mocked(fetchAuthStatus).mockRejectedValue(httpError(404));
    renderChrome();

    expect(
      await screen.findByText("The server returned an unexpected response"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Cannot reach the server")).not.toBeInTheDocument();
  });

  it("retries on its own without flickering back to Loading", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(fetchAuthStatus)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockReturnValueOnce(new Promise(() => {}));
    renderChrome();

    expect(await screen.findByText("Cannot reach the server")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });
    // Second probe is still pending: the failure screen stays up instead of "Loading…".
    expect(fetchAuthStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Cannot reach the server")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("lets the login page render on a 401 probe", async () => {
    pathname = "/login";
    vi.mocked(fetchAuthStatus).mockRejectedValue(httpError(403));
    renderChrome();

    expect(await screen.findByText("content")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
