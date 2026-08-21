import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AuthError, SupabaseClient } from "@supabase/supabase-js";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { LoginForm } from "@/components/auth/LoginForm";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: replaceMock }),
}));

type SignInResult = { data: unknown; error: AuthError | null };

function fakeSupabase(signIn: () => Promise<SignInResult>): SupabaseClient {
  return { auth: { signInWithPassword: signIn } } as unknown as SupabaseClient;
}

const CREDENTIALS = { email: "member@example.com", password: "correct-horse" };

async function fillAndSubmit() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Email"), CREDENTIALS.email);
  await user.type(screen.getByLabelText("Password"), CREDENTIALS.password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  replaceMock.mockClear();
});

describe("LoginForm", () => {
  it("signs in and redirects to chat on success", async () => {
    const client = fakeSupabase(() =>
      Promise.resolve({ data: { session: { user: { email: CREDENTIALS.email } } }, error: null }),
    );
    render(<LoginForm client={client} />);
    await fillAndSubmit();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/chat"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays on the page with an error on a wrong password", async () => {
    const client = fakeSupabase(() =>
      Promise.resolve({
        data: { user: null, session: null },
        error: { name: "AuthApiError", message: "Invalid login credentials", status: 400 } as AuthError,
      }),
    );
    render(<LoginForm client={client} />);
    await fillAndSubmit();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/doesn't match/i);
    expect(replaceMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("shows an offline error without crashing when supabase is unreachable", async () => {
    const client = fakeSupabase(() => Promise.reject(new TypeError("fetch failed")));
    render(<LoginForm client={client} />);
    await fillAndSubmit();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/can't reach the sign-in service/i);
    expect(replaceMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("carries no keys or secrets in the rendered surface", () => {
    const client = fakeSupabase(() => Promise.resolve({ data: {}, error: null }));
    const { container } = render(<LoginForm client={client} />);
    const html = container.innerHTML;
    for (const banned of ["lsv2", "sk-", "service_role", "LANGSMITH", "anon_key", "supabase.co"]) {
      expect(html).not.toContain(banned);
    }
  });
});

describe("LoginForm machine style-token assertions", () => {
  it("uses the birch canvas and Nymble type tokens from the design system", () => {
    const client = fakeSupabase(() => Promise.resolve({ data: {}, error: null }));
    const { container } = render(<LoginForm client={client} />);

    const section = container.querySelector("section");
    expect(section?.getAttribute("style")).toContain("var(--birch)");

    const heading = container.querySelector("h1");
    expect(heading?.getAttribute("style")).toContain("var(--font-headline)");
    expect(heading?.textContent).toBe("Welcome back");

    const wordmark = screen.getByText("nymble");
    expect(wordmark.getAttribute("style")).toContain("var(--rust)");

    const button = screen.getByRole("button", { name: "Sign in" });
    expect(button.className).toContain("btn");
    expect(button.className).toContain("btn-primary");

    for (const input of [screen.getByLabelText("Email"), screen.getByLabelText("Password")]) {
      expect(input.className).toContain("form-input");
    }

    const errorTokenUsed = container.querySelector('[style*="var(--error)"]');
    expect(errorTokenUsed).toBeNull();
  });

  it("ships the verbatim birch/carrot tokens and the carrot primary button", () => {
    const colorsCss = readFileSync(resolve(process.cwd(), "src/design/tokens/colors.css"), "utf8");
    expect(colorsCss).toContain("--birch: #faf3e3");
    expect(colorsCss).toContain("--carrot: #ea492a");
    expect(colorsCss).toContain("--carrot-accessible: #c2390f");

    const componentsCss = readFileSync(
      resolve(process.cwd(), "src/design/components/components.css"),
      "utf8",
    );
    expect(componentsCss).toMatch(/\.btn-primary\s*{[^}]*var\(--carrot\)/);
  });
});
