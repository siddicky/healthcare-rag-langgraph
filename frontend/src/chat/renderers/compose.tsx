"use client";

import { useRef } from "react";
import { useRenderTool } from "@copilotkit/react-core/v2/headless";
import { z } from "zod";
import type { DispatchHandlers } from "@/catalog/dispatch";
import type { DataEnvelope } from "@/catalog/envelopes";
import { CatalogTree } from "@/catalog/render";
import { ComposedNodeSchema } from "@/catalog/schemas";
import { ToolCallCard } from "@/chat/components/ToolCallCard";
import { chatTelemetry } from "@/chat/stream";

/**
 * The CopilotKit `compose_ui` tool-call renderer.
 *
 * The tool call's `parameters.tree` is the raw compose_ui wire tree; it goes
 * through the EXISTING fail-closed catalog pipeline (`<CatalogTree/>` —
 * per-node zod wire validation, dispatch-id check, same-turn `__ref`
 * hydration against the turn's DATA envelopes, concrete re-validation).
 * Envelopes and the turn scope id come from the SAME turn projection the
 * ChatShell uses today (`projectAgentMessages` + `buildTurns`), resolved by
 * the injected `TurnContextResolver` — this module never re-parses messages.
 */

export const ComposeUiParametersSchema = z.object({
  tree: z.union([ComposedNodeSchema, z.array(ComposedNodeSchema)]),
});

/** What a renderer needs to know about the turn owning a tool call. */
export interface ToolTurnContext {
  /** Every DATA envelope parsed from the turn's ToolMessages. */
  readonly envelopes: readonly DataEnvelope[];
  /** The turn's hydration scope id ("" when unknown — refs stay unresolved). */
  readonly scopeId: string;
  /** True when the correlated ToolMessage carried error status. */
  readonly toolErrored: boolean;
}

export const NO_TURN_CONTEXT: ToolTurnContext = { envelopes: [], scopeId: "", toolErrored: false };

export type TurnContextResolver = (toolCallId: string) => ToolTurnContext;

export interface ComposeUiToolViewProps {
  status: "inProgress" | "executing" | "complete";
  parameters: unknown;
  result: string | undefined;
  toolCallId: string;
  resolveTurn: TurnContextResolver;
  handlers?: DispatchHandlers;
}

/** The existing in-flight shimmer card, reused verbatim for loading states. */
export function RendererLoadingCard({ name }: { name: string }) {
  return (
    <div className="widget-wrap">
      <ToolCallCard call={{ id: `loading-${name}`, name, status: "running" }} />
    </div>
  );
}

export function ComposeUiToolView({
  status,
  parameters,
  result,
  toolCallId,
  resolveTurn,
  handlers,
}: ComposeUiToolViewProps) {
  if (status !== "complete") {
    return <RendererLoadingCard name="compose_ui" />;
  }
  void result;

  const parsed = ComposeUiParametersSchema.safeParse(parameters);
  if (!parsed.success) {
    chatTelemetry({ kind: "unknown_tool", name: "compose_ui", detail: "compose_ui args failed wire validation" });
    return null;
  }

  // Mirror composeTreesForTurn: an errored correlated ToolMessage suppresses
  // the tree entirely (plain-text fallback owns the turn).
  const context = resolveTurn(toolCallId);
  if (context.toolErrored) {
    return null;
  }

  return (
    <div className="widget-wrap" data-testid="compose-tree">
      <CatalogTree
        tree={parsed.data.tree}
        envelopes={context.envelopes}
        turnScopeId={context.scopeId}
        handlers={handlers ?? {}}
      />
    </div>
  );
}

/**
 * Registers the `compose_ui` renderer. The resolver/handlers ride refs so the
 * registration stays stable (useRenderTool re-registers only on deps change)
 * while always reading the latest turn projection.
 */
export function useComposeUiRenderer(resolveTurn: TurnContextResolver, handlers?: DispatchHandlers): void {
  const resolverRef = useRef(resolveTurn);
  resolverRef.current = resolveTurn;
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useRenderTool(
    {
      name: "compose_ui",
      parameters: ComposeUiParametersSchema,
      render: ({ status, parameters, result, toolCallId }) => (
        <ComposeUiToolView
          status={status}
          parameters={parameters}
          result={result}
          toolCallId={toolCallId}
          resolveTurn={(id) => resolverRef.current(id)}
          handlers={handlersRef.current}
        />
      ),
    },
    [],
  );
}
