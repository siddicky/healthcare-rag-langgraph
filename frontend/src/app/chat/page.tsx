"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { CopilotKit as CopilotKitProvider } from "@copilotkit/react-core/v2";
import "./chat.css";
import { ChatShell } from "@/chat/components/ChatShell";
import { CoachToolRenderers } from "@/chat/renderers";
import { createBrowserDeps } from "@/chat/coachClient";
import { getSupabase } from "@/lib/supabase";
import {
  copilotKitHeaders,
  refreshCopilotKitAuthorization,
} from "@/lib/copilotkit-auth";

interface SessionInfo {
  email: string;
}

export default function ChatPage() {
  const router = useRouter();
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let active = true;
    const supabase = getSupabase();
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (!active) return;
        const email = data.session?.user.email;
        if (email === undefined) {
          router.replace("/login");
          return;
        }
        return refreshCopilotKitAuthorization(supabase).then(() => {
          if (!active) return;
          setSession({ email });
          setChecked(true);
        });
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
    <CopilotKitProvider
      runtimeUrl="/api/copilotkit"
      agent="coach"
      useSingleEndpoint={false}
      headers={copilotKitHeaders}
    >
      <CoachToolRenderers />
      <ChatShell
        deps={createBrowserDeps()}
        email={session.email}
        onSignedOut={() => router.replace("/login")}
      />
    </CopilotKitProvider>
  );
}
