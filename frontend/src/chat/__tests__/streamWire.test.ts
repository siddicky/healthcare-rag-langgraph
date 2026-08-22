import { describe, expect, it } from "vitest";
import { applyStreamPart } from "../stream";

const calendarInterrupt = [
  {
    value: {
      eventLabel: "Friday check-in",
      fromLabel: "Not scheduled",
      toLabel: "Fri, Aug 28 · 9:00 AM UTC",
      reason: "Add this event to your schedule.",
      status: "pending",
    },
    id: "b4fa0d339d81f8276c0b7480a3811ccc",
  },
];

describe("applyStreamPart against the real Agent Server wire shapes", () => {
  it("reads interrupts embedded in an updates event", () => {
    const delta = applyStreamPart(
      [{ type: "human", id: "h1", content: "schedule my weekly friday check-in" }],
      { event: "updates", data: { __interrupt__: calendarInterrupt } },
    );
    expect(delta.interruptValue).toEqual(calendarInterrupt[0]!.value);
    expect(delta.messages).toHaveLength(1);
  });

  it("still reads the standalone __interrupt__ event shape", () => {
    const delta = applyStreamPart([], { event: "__interrupt__", data: calendarInterrupt });
    expect(delta.interruptValue).toEqual(calendarInterrupt[0]!.value);
  });

  it("applies node updates from the same updates event kind", () => {
    const delta = applyStreamPart([], {
      event: "updates",
      data: {
        coach_gate: {
          messages: [
            {
              type: "ai",
              id: "a1",
              content: "Here is your month.",
              additional_kwargs: {},
              response_metadata: {},
            },
          ],
        },
      },
    });
    expect(delta.interruptValue).toBeNull();
    expect(delta.messages).toHaveLength(1);
  });
});
