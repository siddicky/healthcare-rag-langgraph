import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CatalogTree, resolveCatalogTree } from "../render";
import { telemetrySink, type TelemetryEvent } from "../telemetry";
import { parseDataEnvelope, type DataEnvelope } from "../envelopes";
import type { DataRef } from "../dataRef";

const SCOPE = "a".repeat(64);
const OLD_SCOPE = "b".repeat(64);

function envelope(block_id: string, data: unknown, scope: string = SCOPE): DataEnvelope {
  return { turn_scope_id: scope, block_id, data, text: "coach note" };
}

function ref(pointer: string, block_id = "trend:weight", scope: string = SCOPE): DataRef {
  return { __ref: { turn_scope_id: scope, block_id, pointer } };
}

let events: TelemetryEvent[];

beforeEach(() => {
  events = [];
  telemetrySink.emit = (event) => {
    events.push(event);
  };
});

afterEach(() => {
  telemetrySink.emit = (event) => console.warn("[coach-ui]", event);
});

describe("catalog render pipeline", () => {
  it("renders hydrated values from valid refs", () => {
    const tree = [
      {
        component: "Card",
        props: { text: "This week" },
        children: [
          { component: "Label", props: { gold: true, text: "Progress" } },
          {
            component: "TrendCard",
            props: {
              label: ref("/label"),
              value: ref("/value"),
              delta: ref("/delta"),
              deltaGood: ref("/deltaGood"),
              points: ref("/points"),
            },
          },
        ],
      },
    ];
    const envelopes = [
      envelope("trend:weight", {
        label: "Weight",
        value: "182.4",
        delta: "-0.6 lb this week",
        deltaGood: true,
        points: [189, 188, 186.5, 185, 184, 183, 182.4],
      }),
    ];
    render(<CatalogTree tree={tree} envelopes={envelopes} turnScopeId={SCOPE} />);

    expect(screen.getByText("This week")).toBeInTheDocument();
    expect(screen.getByText("Progress")).toBeInTheDocument();
    expect(screen.getByText("Weight")).toBeInTheDocument();
    expect(screen.getByText("182.4")).toBeInTheDocument();
    expect(screen.getByText("-0.6 lb this week")).toBeInTheDocument();
    expect(document.querySelector("svg polyline")).toBeTruthy();
    expect(events).toEqual([]);
  });

  it("renders the shell without the fact when a ref is unresolved", () => {
    const tree = [
      {
        component: "Card",
        props: { text: "Your week" },
        children: [
          {
            component: "TrendCard",
            props: { label: ref("/label"), value: ref("/value"), points: ref("/points") },
          },
        ],
      },
    ];
    render(<CatalogTree tree={tree} envelopes={[]} turnScopeId={SCOPE} />);

    expect(screen.getByText("Your week")).toBeInTheDocument();
    expect(screen.queryByText("182.4")).not.toBeInTheDocument();
    expect(screen.queryByText("Weight")).not.toBeInTheDocument();
    expect(events.map((e) => e.kind)).toContain("unresolved_ref");
  });

  it("zod-rejects a literal in a fact prop", () => {
    const tree = [
      {
        component: "TrendCard",
        props: { label: ref("/label"), value: "182.4", points: ref("/points") },
      },
    ];
    const envelopes = [envelope("trend:weight", { label: "Weight", points: [189, 188] })];
    const { container } = render(
      <CatalogTree tree={tree} envelopes={envelopes} turnScopeId={SCOPE} />,
    );

    expect(container.querySelector(".card")).toBeNull();
    expect(events.map((e) => e.kind)).toContain("wire_rejection");
  });

  it("renders nothing with telemetry for an unknown component", () => {
    const tree = [{ component: "ProgressBar", props: { percent: 42 } }];
    const { container } = render(
      <CatalogTree tree={tree} envelopes={[]} turnScopeId={SCOPE} />,
    );

    expect(container.innerHTML).toBe("");
    expect(events).toEqual([{ kind: "unknown_component", component: "ProgressBar" }]);
  });

  it("renders nothing with telemetry for an unknown dispatch id", () => {
    const tree = [{ component: "Button", props: { label: "Log it", action: "explode" } }];
    const { container } = render(
      <CatalogTree tree={tree} envelopes={[]} turnScopeId={SCOPE} handlers={{}} />,
    );

    expect(container.innerHTML).toBe("");
    expect(events).toEqual([{ kind: "unknown_dispatch", component: "Button", action: "explode" }]);
  });

  it("rejects a cross-turn ref: a prior turn's same-block envelope cannot hydrate", () => {
    const staleRef = ref("/value", "trend:weight", OLD_SCOPE);
    const tree = [
      {
        component: "TrendCard",
        props: { label: ref("/label", "trend:weight", OLD_SCOPE), value: staleRef, points: ref("/points", "trend:weight", OLD_SCOPE) },
      },
    ];
    const envelopes = [
      envelope("trend:weight", { label: "Weight", value: "190.0", points: [190, 189] }, OLD_SCOPE),
    ];
    const { container } = render(
      <CatalogTree tree={tree} envelopes={envelopes} turnScopeId={SCOPE} />,
    );

    expect(container.querySelector(".card")).toBeNull();
    expect(events.map((e) => e.kind)).toContain("cross_turn_ref");
  });

  it("re-validates hydrated types: a pointer at the wrong shape drops the node", () => {
    const tree = [
      {
        component: "TrendCard",
        props: { label: ref("/label"), value: ref("/points"), points: ref("/points") },
      },
    ];
    const envelopes = [envelope("trend:weight", { label: "Weight", points: [189, 188] })];
    const { container } = render(
      <CatalogTree tree={tree} envelopes={envelopes} turnScopeId={SCOPE} />,
    );

    expect(container.querySelector(".card")).toBeNull();
    expect(events.map((e) => e.kind)).toContain("hydrate_rejection");
  });

  it("hydrates InjectionTracker through the sparse-to-seven adapter", () => {
    const tree = [
      {
        component: "InjectionTracker",
        props: {
          medicationName: ref("/medicationName", "weekstrip:injection"),
          doseLabel: ref("/doseLabel", "weekstrip:injection"),
          days: ref("/days", "weekstrip:injection"),
          nextDoseLabel: ref("/nextDoseLabel", "weekstrip:injection"),
        },
      },
    ];
    const envelopes = [
      envelope("weekstrip:injection", {
        medicationName: "Semaglutide",
        doseLabel: "1.0 mg",
        nextDoseLabel: "Thursday",
        days: [{ date: "2026-08-20", status: "logged" }],
      }),
    ];
    render(<CatalogTree tree={tree} envelopes={envelopes} turnScopeId={SCOPE} />);

    expect(screen.getByText("Semaglutide")).toBeInTheDocument();
    expect(screen.getByText("1.0 mg")).toBeInTheDocument();
    expect(screen.getByText("Thursday")).toBeInTheDocument();
    for (const label of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) {
      expect(screen.getByText(label, { exact: true })).toBeInTheDocument();
    }
    expect(screen.getByText("✓")).toBeInTheDocument();
  });

  it("triggers the registered handler when dispatching a known action", async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    const tree = [{ component: "Button", props: { label: "Log weight", action: "log_weight" } }];
    render(
      <CatalogTree tree={tree} envelopes={[]} turnScopeId={SCOPE} handlers={{ log_weight: handler }} />,
    );

    await user.click(screen.getByRole("button", { name: "Log weight" }));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(events).toEqual([]);
  });

  it("keeps siblings when one node fails", () => {
    const tree = [
      { component: "Tag", props: { text: "On track" } },
      { component: "Button", props: { label: "Broken", action: "nope" } },
      { component: "Tag", props: { text: "Missed" } },
    ];
    render(<CatalogTree tree={tree} envelopes={[]} turnScopeId={SCOPE} />);

    expect(screen.getByText("On track")).toBeInTheDocument();
    expect(screen.getByText("Missed")).toBeInTheDocument();
    expect(screen.queryByText("Broken")).not.toBeInTheDocument();
    expect(events.map((e) => e.kind)).toEqual(["unknown_dispatch"]);
  });
});

describe("resolveCatalogTree (pure)", () => {
  it("produces a json-render spec with flat element keys", () => {
    const tree = [
      {
        component: "Card",
        props: { text: "Hi" },
        children: [{ component: "Tag", props: { text: "On track" } }],
      },
    ];
    const result = resolveCatalogTree(tree, [], SCOPE);
    expect(result.roots).toHaveLength(1);
    const root = result.elements[result.roots[0] ?? ""] ?? null;
    expect(root?.type).toBe("Card");
    expect(root?.children).toHaveLength(1);
    expect(result.elements[root?.children?.[0] ?? ""]?.type).toBe("Tag");
    expect(result.events).toEqual([]);
  });
});

describe("parseDataEnvelope", () => {
  it("parses a canonical make_envelope payload", () => {
    const content = JSON.stringify({
      turn_scope_id: SCOPE,
      block_id: "trend:weight",
      data: { value: "182.4" },
      text: "logged",
    });
    expect(parseDataEnvelope(content)).toEqual({
      turn_scope_id: SCOPE,
      block_id: "trend:weight",
      data: { value: "182.4" },
      text: "logged",
    });
  });

  it("returns null for non-envelope content", () => {
    expect(parseDataEnvelope("plain coach text")).toBeNull();
    expect(parseDataEnvelope(42)).toBeNull();
    expect(parseDataEnvelope(null)).toBeNull();
    expect(parseDataEnvelope(JSON.stringify({ block_id: "x" }))).toBeNull();
  });
});
