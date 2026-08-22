"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { AuthError, SupabaseClient } from "@supabase/supabase-js";
import { Button } from "@/components/core/Button";
import { Card } from "@/components/core/Card";
import { Label } from "@/components/core/Label";
import { getSupabase } from "@/lib/supabase";

function loginErrorMessage(error: AuthError): string {
  if (error.message.toLowerCase().includes("invalid login credentials")) {
    return "That email and password combination doesn't match. Double-check and try again.";
  }
  return "We couldn't sign you in. Please try again.";
}

export interface LoginFormProps {
  /** Injectable for tests; defaults to the shared browser client. */
  client?: SupabaseClient;
  redirectTo?: string;
}

export function LoginForm({ client, redirectTo = "/chat" }: LoginFormProps) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      const auth = (client ?? getSupabase()).auth;
      const { error: signInError } = await auth.signInWithPassword({ email, password });
      if (signInError) {
        setError(loginErrorMessage(signInError));
        setPending(false);
        return;
      }
      router.replace(redirectTo);
    } catch (err) {
      setError(
        err instanceof Error && err.message.startsWith("Sign-in is not configured")
          ? err.message
          : "We can't reach the sign-in service right now. Check your connection and try again.",
      );
      setPending(false);
    }
  }

  return (
    <section
      style={{
        minHeight: "100vh",
        background: "var(--birch)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "var(--space-md)",
      }}
    >
      <Card hoverLift={false} style={{ width: "100%", maxWidth: 400 }}>
        <div style={{ marginBottom: "var(--space-sm)" }}>
          <span
            style={{
              fontFamily: "var(--font-headline)",
              fontSize: 26,
              fontWeight: 700,
              color: "var(--rust)",
            }}
          >
            nymble
          </span>
        </div>
        <Label>Nymble coach</Label>
        <h1
          style={{
            fontFamily: "var(--font-headline)",
            fontSize: "var(--text-h3)",
            letterSpacing: "var(--tracking-display)",
            color: "var(--rust)",
            margin: "var(--space-xs) 0 var(--space-xs)",
          }}
        >
          Welcome back
        </h1>
        <p style={{ color: "var(--camel)", fontSize: 15, margin: 0 }}>
          Sign in to text with your coach. No app, no dashboard — just the conversation you left off.
        </p>
        <form onSubmit={handleSubmit} noValidate style={{ marginTop: "var(--space-md)" }}>
          <div className="form-group">
            <label className="form-label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              className="form-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error !== null && (
            <p
              role="alert"
              style={{ color: "var(--error)", fontSize: 14, margin: "0 0 var(--space-sm)" }}
            >
              {error}
            </p>
          )}
          <Button type="submit" full disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </Card>
    </section>
  );
}
