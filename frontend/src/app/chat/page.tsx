"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import "./chat.css";
import { ChatShell } from "@/chat/components/ChatShell";
import { createBrowserDeps } from "@/chat/coachClient";
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

  if (!checked || session === null) {
    return (
      <section style={{ minHeight: "100vh", background: "var(--birch)" }} aria-busy="true" />
    );
  }

  return (
    <ChatShell
      deps={createBrowserDeps()}
      email={session.email}
      onSignedOut={() => router.replace("/login")}
    />
  );
}
