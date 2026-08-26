"use client";

import { useCallback, useMemo, useRef } from "react";
import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2/headless";
import type { DispatchHandlers } from "@/catalog/dispatch";
import { buildTurns, isToolMessage, type TurnModel } from "@/chat/model";
import { projectAgentMessages } from "@/chat/useCoachStream";
import { useComposeUiRenderer, NO_TURN_CONTEXT, type ToolTurnContext } from "./compose";
import { useEnvelopeToolRenderers } from "./envelope-tools";
import { registerMedicalRenderers } from "./medical";
import { registerCatchAllRenderer } from "./catch-all";
import { registerInterruptHandlers } from "./interrupts";
import { registerClipboardFrontendTool } from "./clipboard";

/**
 * The single registration entry for every CopilotKit tool-call renderer.
 *
 * `<CoachToolRenderers/>` renders nothing; mounting it inside the CopilotKit
 * provider registers:
 *   - compose_ui            -> the fail-closed catalog pipeline (compose.tsx)
 *   - envelope tools        -> TrendCard / InjectionTracker / MiniCalendar /
 *                              compact ReminderCard list (envelope-tools.tsx)
 *   - medical + catch-all   -> todo 8 (`./medical`, `./catch-all`)
 *   - interrupts + clipboard-> todo 9 (`./interrupts`, `./clipboard`)
 *
 * The turn context (DATA envelopes + scope id per tool call) comes from the
 * SAME projection ChatShell uses: `projectAgentMessages` + `buildTurns`.
 */

function turnContextFor(turns: readonly TurnModel[], toolCallId: string): ToolTurnContext {
  for (const turn of turns) {
    const ids = new Set<string>();
    let toolErrored = false;
    for (const message of turn.messages) {
      if (Array.isArray(message.tool_calls)) {
        for (const call of message.tool_calls) ids.add(call.id);
      }
      if (isToolMessage(message) && typeof message.tool_call_id === "string") {
        ids.add(message.tool_call_id);
        if (message.tool_call_id === toolCallId && message.status === "error") {
          toolErrored = true;
        }
      }
    }
    if (ids.has(toolCallId)) {
      return { envelopes: turn.envelopes, scopeId: turn.scopeId ?? "", toolErrored };
    }
  }
  return NO_TURN_CONTEXT;
}

export function CoachToolRenderers() {
  const { agent } = useAgent({
    agentId: "coach",
    updates: [UseAgentUpdate.OnMessagesChanged],
  });

  const turns = useMemo(() => buildTurns(projectAgentMessages(agent.messages)), [agent.messages]);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;

  const resolveTurn = useCallback(
    (toolCallId: string): ToolTurnContext => turnContextFor(turnsRef.current, toolCallId),
    [],
  );

  useComposeUiRenderer(resolveTurn);
  useEnvelopeToolRenderers();
  registerMedicalRenderers();
  registerCatchAllRenderer();
  const interruptSurface = registerInterruptHandlers();
  registerClipboardFrontendTool();

  return <>{interruptSurface}</>;
}
