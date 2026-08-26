import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  ComposeUiToolView,
  NO_TURN_CONTEXT,
  type ToolTurnContext,
  type TurnContextResolver,
} from "@/chat/renderers/compose";
import { chatTelemetrySink, type ChatTelemetryEvent } from "@/chat/stream";
import { telemetrySink, type TelemetryEvent } from "@/catalog/telemetry";

const SCOPE = "scope-1";

const TREND_ENVELOPE = {
  turn_scope_id: SCOPE,
  block_id: "trend:weight",
  data: { label: "Weight", value: "182.4", unit: "kg", delta: "-2.0 kg", deltaGood: true, points: [189, 188, 186.5, 185, 184, 183, 182.4] },
  text: "Logged.",
};

function trendTree() {
  return [
    {
      component: "Card",
      props: { text: "This week" },
      children: [
        {
          component: "TrendCard",
          props: {
            label: { __ref: { turn_scope_id: SCOPE, block_id: "trend:weight", pointer: "/label" } },
            value: { __ref: { turn_scope_id: SCOPE, block_id: "trend:weight", pointer: "/value" } },
            points: { __ref: { turn_scope_id: SCOPE, block_id: "trend:weight", pointer: "/points" } },
          },
        },
      ],
    },
  ];
}

function resolverWith(overrides: Partial<ToolTurnContext> = {}): TurnContextResolver {
  return () => ({ envelopes: [TREND_ENVELOPE], scopeId: SCOPE, toolErrored: false, ...overrides });
}

describe("ComposeUiToolView (copilotkit compose_ui renderer)", () => {
  let chatEvents: ChatTelemetryEvent[];
  let catalogEvents: TelemetryEvent[];

  beforeEach(() => {
    chatEvents = [];
    catalogEvents = [];
    vi.spyOn(chatTelemetrySink, "emit").mockImplementation((event) => {
      chatEvents.push(event);
    });
    vi.spyOn(telemetrySink, "emit").mockImplementation((event) => {
      catalogEvents.push(event);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the hydrated tree from the turn's envelopes when complete", () => {
    render(
      <ComposeUiToolView
        status="complete"
        parameters={{ tree: trendTree() }}
        result={JSON.stringify(TREND_ENVELOPE)}
        toolCallId="c1"
        resolveTurn={resolverWith()}
      />,
    );
    expect(screen.getByTestId("compose-tree")).toBeInTheDocument();
    expect(screen.getByText("This week")).toBeInTheDocument();
    expect(screen.getByText("182.4")).toBeInTheDocument();
    expect(document.querySelector("svg polyline")).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("__ref");
    expect(document.body.textContent ?? "").not.toContain("turn_scope_id");
  });

  it("shows the existing shimmer card while inProgress or executing", () => {
    for (const status of ["inProgress", "executing"] as const) {
      const { unmount } = render(
        <ComposeUiToolView
          status={status}
          parameters={{ tree: trendTree() }}
          result={undefined}
          toolCallId="c1"
          resolveTurn={resolverWith()}
        />,
      );
      expect(screen.getByTestId("tool-call-pending")).toBeInTheDocument();
      expect(screen.queryByTestId("compose-tree")).toBeNull();
      unmount();
    }
  });

  it("suppresses the tree when the correlated ToolMessage errored", () => {
    render(
      <ComposeUiToolView
        status="complete"
        parameters={{ tree: trendTree() }}
        result={JSON.stringify(TREND_ENVELOPE)}
        toolCallId="c1"
        resolveTurn={resolverWith({ toolErrored: true })}
      />,
    );
    expect(screen.queryByTestId("compose-tree")).toBeNull();
    expect(screen.queryByText("182.4")).toBeNull();
  });

  it("renders nothing + chatTelemetry for args that fail the wire schema", () => {
    render(
      <ComposeUiToolView
        status="complete"
        parameters={{ nope: true }}
        result={undefined}
        toolCallId="c1"
        resolveTurn={resolverWith()}
      />,
    );
    expect(screen.queryByTestId("compose-tree")).toBeNull();
    expect(chatEvents).toEqual([
      expect.objectContaining({ kind: "unknown_tool", name: "compose_ui" }),
    ]);
  });

  it("renders the shell but never an unresolved ref — fail-closed with telemetry", () => {
    const staleEnvelope = {
      turn_scope_id: SCOPE,
      block_id: "trend:bmi",
      data: { label: "BMI", value: "27.1", points: [28, 27.5, 27.1] },
      text: "Logged.",
    };
    render(
      <ComposeUiToolView
        status="complete"
        parameters={{ tree: trendTree() }}
        result={JSON.stringify(staleEnvelope)}
        toolCallId="c1"
        resolveTurn={() => ({ envelopes: [staleEnvelope], scopeId: SCOPE, toolErrored: false })}
      />,
    );
    expect(screen.getByTestId("compose-tree")).toBeInTheDocument();
    expect(screen.getByText("This week")).toBeInTheDocument();
    expect(screen.queryByText("182.4")).toBeNull();
    expect(screen.queryByText("Weight")).toBeNull();
    expect(catalogEvents.some((event) => event.kind === "unresolved_ref")).toBe(true);
  });

  it("an unknown turn resolves to the empty context — refs stay unresolved", () => {
    const probe: ToolTurnContext = NO_TURN_CONTEXT;
    expect(probe.scopeId).toBe("");
    expect(probe.envelopes).toHaveLength(0);
    expect(probe.toolErrored).toBe(false);
  });
});
