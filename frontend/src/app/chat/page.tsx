"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/core/Button";
import { Card } from "@/components/core/Card";
import { Label } from "@/components/core/Label";
import { getSupabase } from "@/lib/supabase";

interface SessionInfo {
  email: string;
}

export default function ChatPage() {
  const router = useRouter();
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let active = true;
    getSupabase()
      .auth.getSession()
      .then(({ data }) => {
        if (!active) return;
        const email = data.session?.user.email;
        if (email === undefined) {
          router.replace("/login");
          return;
        }
        setSession({ email });
        setChecked(true);
      })
      .catch(() => {
        if (active) router.replace("/login");
      });
    return () => {
      active = false;
    };
  }, [router]);

  async function signOut() {
    await getSupabase().auth.signOut();
    router.replace("/login");
  }

  if (!checked) {
    return (
      <section style={{ minHeight: "100vh", background: "var(--birch)" }} aria-busy="true" />
    );
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
      <Card hoverLift={false} style={{ width: "100%", maxWidth: 480, textAlign: "center" }}>
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
        <div style={{ marginTop: "var(--space-xs)", marginBottom: "var(--space-xs)" }}>
          <Label>Nymble coach</Label>
        </div>
        <p style={{ color: "var(--camel)", fontSize: 15, margin: "0 0 var(--space-md)" }}>
          Signed in as {session?.email}
        </p>
        <Button variant="secondary" size="sm" onClick={() => void signOut()}>
          Sign out
        </Button>
      </Card>
    </section>
  );
}
