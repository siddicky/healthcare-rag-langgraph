"use client";

import { z } from "zod";
import {
  useFrontendTool,
} from "@copilotkit/react-core/v2/headless";
import { copyToClipboardExecute } from "@/chat/useCoachStream";

/**
 * Client-side `copy_to_clipboard` via useFrontendTool (plan todo 9).
 *
 * The execute is the EXISTING `copyToClipboardExecute` from useCoachStream —
 * same semantics, unchanged: model-triggered, immediate copy (navigator
 * clipboard → hidden-textarea execCommand fallback), fail-closed error
 * strings that never echo the copied text. NO confirmation popup — recorded
 * owner decision. The server-side tool stays as-is; with this registration
 * AG-UI executes the call in the browser and the server interrupt path does
 * not trigger for this frontend (the server tool tolerates an already-copied
 * resume by normalizing arbitrary resume values).
 */

const ClipboardArgsSchema = z.object({
  text: z.string(),
});

/** Null-component: registers the frontend tool with the CopilotKit core. */
export function registerClipboardFrontendTool(): null {
  useFrontendTool({
    name: "copy_to_clipboard",
    description: "Copy text to the member's clipboard (client-side)",
    parameters: ClipboardArgsSchema,
    handler: copyToClipboardExecute,
    agentId: "coach",
  });
  return null;
}
