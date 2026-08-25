import { beforeEach, describe, expect, it, vi } from "vitest";
import { chatTelemetrySink } from "@/chat/stream";
import { applyStreamPart } from "@/chat/stream";
import {
  buildTurns,
  classifyInterruptPayload,
  composeTreesForTurn,
  containsEraseMarker,
  firstInterruptValue,
  isEraseMarker,
  mergeMessages,
  regenerateEligibility,
  toWireMessages,
  type WireMessage,
} from "@/chat/model";
import { ERASE_MARKER_NAME, SENTINEL_QUESTION } from "@/chat/coachProtocol";
import { envelopeString } from "./helpers";

describe("applyStreamPart (updates-only)", () => {
  beforeEach(() => {
    chatTelemetrySink.emit = vi.fn();
  });

  it("renders messages from allow-listed nodes", () => {
    const delta = applyStreamPart([], {
      event: "updates",
      data: { finalize_coach: { messages: [{ type: "ai", id: "a1", content: "Hello" }] } },
    });
    expect(delta.messages).toHaveLength(1);
    expect(delta.messages[0]?.id).toBe("a1");
  });

  it("drops unknown node updates entirely (never renders)", () => {
    const delta = applyStreamPart([], {
      event: "updates",
      data: { some_future_node: { messages: [{ type: "ai", id: "x", content: "nope" }] } },
    });
    expect(delta.messages).toHaveLength(0);
    expect(chatTelemetrySink.emit).toHaveBeenCalledWith({ kind: "unknown_node", node: "some_future_node" });
  });

  it("ignores human messages from the stream (the human bubble is a local echo)", () => {
    const delta = applyStreamPart([], {
      event: "updates",
      data: { coach_gate: { messages: [{ type: "human", id: "h1", content: "scrubbed" }] } },
    });
    expect(delta.messages).toHaveLength(0);
  });

  it("dedupes finalize's whole-channel re-projection by message id", () => {
    const withAi = applyStreamPart([], {
      event: "updates",
      data: { coach_agent: { messages: [{ type: "ai", id: "a1", content: "answer" }] } },
    });
    const finalize = applyStreamPart(withAi.messages, {
      event: "updates",
      data: {
        finalize_coach: {
          messages: [
            { type: "human", id: "h1", content: "q" },
            { type: "ai", id: "a1", content: "answer" },
          ],
        },
      },
    });
    expect(finalize.messages.filter((m) => m.id === "a1")).toHaveLength(1);
    expect(finalize.messages).toHaveLength(1);
  });

  it("captures the single __interrupt__ value", () => {
    const delta = applyStreamPart([], {
      event: "__interrupt__",
      data: [{ value: { eventLabel: "Check-in", fromLabel: "A", toLabel: "B" } }],
    });
    expect(delta.interruptValue).toEqual({ eventLabel: "Check-in", fromLabel: "A", toLabel: "B" });
  });

  it("ignores metadata and non-updates events", () => {
    const delta = applyStreamPart([{ type: "ai", id: "a1", content: "x" }], {
      event: "metadata",
      data: { run_id: "r" },
    });
    expect(delta.messages).toHaveLength(1);
    expect(delta.interruptValue).toBeNull();
  });

  it("tolerates malformed updates data without crashing", () => {
    const delta = applyStreamPart([], { event: "updates", data: "garbage" });
    expect(delta.messages).toHaveLength(0);
  });
});

describe("turn building and envelopes", () => {
  it("bounds turns by human messages", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "first question" },
      { type: "ai", id: "a1", content: "first answer" },
      { type: "human", id: "h2", content: "second question" },
      { type: "ai", id: "a2", content: "second answer" },
    ];
    const turns = buildTurns(messages);
    expect(turns).toHaveLength(2);
    expect(turns[0]?.human?.id).toBe("h1");
    expect(turns[1]?.messages.map((m) => m.id)).toEqual(["a2"]);
  });

  it("collects DATA envelopes into the owning turn and derives its scope", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "log my weight" },
      { type: "ai", id: "a1", content: "", tool_calls: [{ id: "c1", name: "log_metric", args: {} }] },
      {
        type: "tool",
        id: "t1",
        tool_call_id: "c1",
        content: envelopeString("trend:weight", { value: "182.4" }, "scope-A"),
      },
    ];
    const turns = buildTurns(messages);
    expect(turns[0]?.envelopes).toHaveLength(1);
    expect(turns[0]?.envelopes[0]?.block_id).toBe("trend:weight");
    expect(turns[0]?.scopeId).toBe("scope-A");
  });

  it("renders compose_ui trees only after a correlated successful ToolMessage", () => {
    const call = { id: "c1", name: "compose_ui", args: { tree: [{ component: "Card", props: {} }] } };
    const errorOnly: WireMessage[] = [
      { type: "human", id: "h1", content: "show" },
      { type: "ai", id: "a1", content: "", tool_calls: [call] },
      { type: "tool", id: "t1", tool_call_id: "c1", content: "", status: "error" },
    ];
    const errorTurn = buildTurns(errorOnly)[0];
    expect(errorTurn !== undefined && composeTreesForTurn(errorTurn)).toHaveLength(0);

    const success: WireMessage[] = [
      { type: "human", id: "h1", content: "show" },
      { type: "ai", id: "a1", content: "", tool_calls: [call] },
      { type: "tool", id: "t1", tool_call_id: "c1", content: "{}", status: "success" },
    ];
    const successTurn = buildTurns(success)[0];
    const trees = successTurn !== undefined ? composeTreesForTurn(successTurn) : [];
    expect(trees).toHaveLength(1);
    expect(trees[0]?.callId).toBe("c1");
  });
});

describe("erase marker detection", () => {
  it("recognizes the v19 marker by name on an AI message", () => {
    const marker: WireMessage = { type: "ai", id: "m1", name: ERASE_MARKER_NAME, content: "All saved data erased." };
    expect(isEraseMarker(marker)).toBe(true);
    expect(containsEraseMarker([{ type: "ai", id: "x", content: "hi" }])).toBe(false);
  });
});

describe("regenerateEligibility (latest-turn window only)", () => {
  const base = { hasPendingInterrupt: false, attachmentPending: false };

  function turnsFromMessages(messages: WireMessage[]) {
    return buildTurns(messages);
  }

  it("is eligible for the latest plain Q/A turn and re-sends THAT window's question", () => {
    const turns = turnsFromMessages([
      { type: "human", id: "h1", content: "older mutating question" },
      { type: "ai", id: "a1", content: "", tool_calls: [{ id: "c1", name: "log_weight", args: {} }] },
      { type: "tool", id: "t1", tool_call_id: "c1", content: envelopeString("trend:weight", {}) },
      { type: "human", id: "h2", content: "What is a healthy breakfast?" },
      { type: "ai", id: "a2", content: "A protein-forward breakfast…" },
    ]);
    const gate = regenerateEligibility(turns, base);
    expect(gate.eligible).toBe(true);
    expect(gate.question).toBe("What is a healthy breakfast?");
  });

  it("is NOT eligible for any mutating tool family in the latest window", () => {
    for (const name of [
      "log_metric",
      "log_injection",
      "change_schedule",
      "remember_fact",
      "create_reminder",
      "set_reminder",
      "edit_reminder",
      "cancel_reminder",
    ]) {
      const turns = turnsFromMessages([
        { type: "human", id: "h1", content: "do the thing" },
        { type: "ai", id: "a1", content: "", tool_calls: [{ id: "c1", name, args: {} }] },
      ]);
      expect(regenerateEligibility(turns, base).eligible, name).toBe(false);
    }
  });

  it("is NOT eligible when the latest window has ToolMessages even without visible calls", () => {
    const turns = turnsFromMessages([
      { type: "human", id: "h1", content: "show my week" },
      { type: "tool", id: "t1", tool_call_id: "c9", content: envelopeString("weekstrip:injection", {}) },
      { type: "ai", id: "a1", content: "Here is your week." },
    ]);
    expect(regenerateEligibility(turns, base).eligible).toBe(false);
  });

  it("is NOT eligible for the older-safe-turn-then-mutation case (question never crosses windows)", () => {
    const turns = turnsFromMessages([
      { type: "human", id: "h1", content: "safe question" },
      { type: "ai", id: "a1", content: "safe answer" },
      { type: "human", id: "h2", content: "log my weight at 182" },
      { type: "ai", id: "a2", content: "", tool_calls: [{ id: "c1", name: "log_metric", args: {} }] },
    ]);
    const gate = regenerateEligibility(turns, base);
    expect(gate.eligible).toBe(false);
    expect(gate.question).toBeNull();
  });

  it("is NOT eligible for the erase marker, interrupts, attachment turns, or missing answers", () => {
    const markerTurn = turnsFromMessages([
      { type: "human", id: "h1", content: "delete my data" },
      { type: "ai", id: "m1", name: ERASE_MARKER_NAME, content: "All saved data erased." },
    ]);
    expect(regenerateEligibility(markerTurn, base).eligible).toBe(false);

    const sentinelTurn = turnsFromMessages([
      { type: "human", id: "h1", content: SENTINEL_QUESTION },
      { type: "ai", id: "a1", content: "reviewed" },
    ]);
    expect(regenerateEligibility(sentinelTurn, base).eligible).toBe(false);

    const plain = turnsFromMessages([
      { type: "human", id: "h1", content: "hi" },
      { type: "ai", id: "a1", content: "hello" },
    ]);
    expect(regenerateEligibility(plain, { ...base, hasPendingInterrupt: true }).eligible).toBe(false);
    expect(regenerateEligibility(plain, { ...base, attachmentPending: true }).eligible).toBe(false);

    const unanswered = turnsFromMessages([{ type: "human", id: "h1", content: "hi" }]);
    expect(regenerateEligibility(unanswered, base).eligible).toBe(false);
  });
});

describe("interrupt payload classification", () => {
  it("classifies calendar-change cards", () => {
    const kind = classifyInterruptPayload({
      eventLabel: "Friday check-in",
      fromLabel: "Fri · 2 PM",
      toLabel: "Mon · 10 AM",
      reason: "Proposed",
      status: "pending",
    });
    expect(kind.kind).toBe("calendar-change");
  });

  it("classifies memory-extraction payloads", () => {
    const kind = classifyInterruptPayload({
      sourceLabel: ".pdf · 182 bytes",
      fields: [{ key: "name", label: "Full name", value: "Jordan Ellis", needsReview: true }],
    });
    expect(kind.kind).toBe("memory-extraction");
  });

  it("returns unknown for anything else (renders nothing, never crashes)", () => {
    expect(classifyInterruptPayload({ whatever: 1 }).kind).toBe("unknown");
    expect(classifyInterruptPayload("nope").kind).toBe("unknown");
    expect(classifyInterruptPayload(null).kind).toBe("unknown");
  });

  it("extracts the first interrupt value from a projected state array", () => {
    expect(firstInterruptValue([{ value: { a: 1 } }, { value: { a: 2 } }])).toEqual({ a: 1 });
    expect(firstInterruptValue([])).toBeNull();
    expect(firstInterruptValue("x")).toBeNull();
  });
});

describe("latest-state history reads", () => {
  it("parses only the allow-listed messages channel and ignores other values", () => {
    const projected = {
      values: {
        messages: [{ type: "human", id: "h1", content: "q" }],
        route: "route_b",
        follow_ups: ["follow?"],
      },
      interrupts: [],
    };
    const wire = toWireMessages(projected.values.messages);
    expect(wire).toHaveLength(1);
    const turns = buildTurns(wire);
    expect(turns).toHaveLength(1);
    expect(turns[0]?.human?.content).toBe("q");
  });

  it("rejects malformed message entries silently", () => {
    expect(toWireMessages([null, 5, "x", { type: "ai", id: "ok" }])).toHaveLength(1);
  });

  it("mergeMessages keeps insertion order and last-write-wins per id", () => {
    const merged = mergeMessages(
      [
        { type: "human", id: "h1", content: "q" },
        { type: "ai", id: "a1", content: "v1" },
      ],
      [{ type: "ai", id: "a1", content: "v2" }],
    );
    expect(merged.map((m) => (m.id === "a1" ? m.content : m.content))).toEqual(["q", "v2"]);
  });
});
