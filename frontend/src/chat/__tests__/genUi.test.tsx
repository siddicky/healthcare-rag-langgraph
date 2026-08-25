import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatShell } from "@/chat/components/ChatShell";
import { MessageList } from "@/chat/components/MessageList";
import type { CoachApiBundle } from "@/chat/useCoachStream";
import type { ThreadSummary } from "@/chat/coachApi";
import {
  aiDisplayText,
  parseMemoryConfirmation,
  parseReminderDelivery,
  reminderActionTurn,
} from "@/chat/model";
import { applyUploadEvent, type UploadUi } from "@/chat/uploadFlow";
import {
  aiMessage,
  emptyStream,
  fakeDeps,
  fakeStream,
  humanMessage,
  thread,
  toolMessage,
  type StreamCall,
} from "./helpers";

const EMAIL = "member@example.com";

const CALENDAR_INTERRUPT = {
  eventLabel: "Friday check-in",
  fromLabel: "Fri, Aug 22 · 2:00 PM",
  toLabel: "Mon, Aug 25 · 10:00 AM",
  reason: "Monday is open.",
  status: "pending",
};

const MEMORY_FIELDS = [
  { key: "goalWeight", label: "Goal weight", value: "180 lb", needsReview: true },
  { key: "medications", label: "Current medications", value: "Metformin 500mg" },
];

function stateWith(
  messages: unknown[],
  interrupts: unknown[] = [],
): { values: { messages: unknown[] }; interrupts: unknown[] } {
  return { values: { messages }, interrupts };
}

function mountDeps(state: () => { values: { messages: unknown[] }; interrupts: unknown[] }) {
  const api: Partial<CoachApiBundle> = {
    searchThreads: vi.fn(async () => [thread("t-1")] as ThreadSummary[]),
    getThreadState: vi.fn(async () => state()),
  };
  return api;
}

function reminderDeliveryMessage(id: string) {
  const envelope = JSON.stringify({
    turn_scope_id: "scope-r",
    block_id: `reminder:${id}`,
    data: {
      title: "Weekly weight log",
      schedule: "Every Monday at 8:00 AM",
      weekday: "Monday",
      time: "08:00",
      active: true,
      nextRun: "Mon, Aug 24",
    },
    text: "Scheduled reminder.",
  });
  return aiMessage(`This is your scheduled reminder.\n${envelope}`, id);
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("pure card-content parsers", () => {
  it("parses a reminder delivery into its literal + card, hiding the envelope", () => {
    const delivery = parseReminderDelivery(reminderDeliveryMessage("a1").content);
    expect(delivery?.literal).toBe("This is your scheduled reminder.");
    expect(delivery?.card?.title).toBe("Weekly weight log");
    expect(delivery?.card?.weekday).toBe("Monday");
  });

  it("returns null for non-delivery content", () => {
    expect(parseReminderDelivery("plain text")).toBeNull();
    expect(parseReminderDelivery(JSON.stringify({ turn_scope_id: "s", block_id: "trend:weight", data: {}, text: "" }))).toBeNull();
    expect(parseReminderDelivery(null)).toBeNull();
  });

  it("parses the memory confirmation and exposes per-field status", () => {
    const content = JSON.stringify({
      component: "MemoryExtractionCard",
      data: {
        sourceLabel: ".pdf · 182 bytes",
        fields: [
          { key: "goalWeight", label: "Goal weight", value: "180 lb", status: "saved" },
          { key: "medications", label: "Current medications", value: "Metformin 500mg", status: "discarded", notice: "Privacy checks failed; field was not saved." },
        ],
      },
    });
    const confirmation = parseMemoryConfirmation(content);
    expect(confirmation?.data.fields[0]?.status).toBe("saved");
    expect(confirmation?.data.fields[1]?.notice).toContain("Privacy checks");
  });

  it("aiDisplayText never exposes card JSON", () => {
    expect(aiDisplayText(reminderDeliveryMessage("a1").content)).toBe("This is your scheduled reminder.");
    expect(aiDisplayText(JSON.stringify({ component: "MemoryExtractionCard", data: { sourceLabel: "s", fields: [] } }))).toBe("");
    expect(aiDisplayText('{"component":"SomethingNew","data":{}}')).toBe("");
    expect(aiDisplayText("A plain answer.")).toBe("A plain answer.");
  });

  it("reminderActionTurn phrases each full-mode action as a member turn", () => {
    expect(reminderActionTurn("pause", "Weekly weight log")).toBe("Pause my Weekly weight log reminder");
    expect(reminderActionTurn("resume", "Weekly weight log")).toBe("Resume my Weekly weight log reminder");
    expect(reminderActionTurn("cancel", "Weekly weight log")).toBe("Cancel my Weekly weight log reminder");
    expect(reminderActionTurn("move", "Weekly weight log", { weekday: "Wednesday", timeLabel: "7:30 AM" })).toBe(
      "Move my Weekly weight log reminder to Wednesday at 7:30 AM",
    );
  });
});

describe("CalendarChangeCard interrupt surface", () => {
  it("renders pending buttons from a projected interrupt", async () => {
    const deps = fakeDeps(mountDeps(() => stateWith([], [{ value: CALENDAR_INTERRUPT }])), emptyStream());
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByTestId("interrupt-card")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm change" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep original time" })).toBeInTheDocument();
  });

  it("confirm resumes ONCE with exactly {accept:true} — duplicate in-flight clicks no-op, fields never sent", async () => {
    const stream = fakeStream(() => []);
    const deps = fakeDeps(mountDeps(() => stateWith([], [{ value: CALENDAR_INTERRUPT }])), stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const confirm = await screen.findByRole("button", { name: "Confirm change" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    const call = stream.calls[0] as StreamCall;
    expect(call.payload).toEqual({ command: { resume: { accept: true } } });
    if (!("command" in call.payload)) throw new Error("expected a resume payload");
    expect(Object.keys(call.payload.command.resume)).toEqual(["accept"]);
  });

  it("decline resumes with exactly {accept:false}", async () => {
    const stream = fakeStream(() => []);
    const deps = fakeDeps(mountDeps(() => stateWith([], [{ value: CALENDAR_INTERRUPT }])), stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Keep original time" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    const call = stream.calls[0] as StreamCall;
    expect(call.payload).toEqual({ command: { resume: { accept: false } } });
    if (!("command" in call.payload)) throw new Error("expected a resume payload");
    expect(Object.keys(call.payload.command.resume)).toEqual(["accept"]);
  });

  it("re-renders the resolved outcome from a calendar-change envelope — buttons gone, outcome kept", async () => {
    const envelope = JSON.stringify({
      turn_scope_id: "scope-1",
      block_id: "calendar-change:op-1",
      data: { card: { ...CALENDAR_INTERRUPT, status: "confirmed" }, schedule: { monthLabel: "August 2026" } },
      text: "Change confirmed.",
    });
    const deps = fakeDeps(
      mountDeps(() => stateWith([humanMessage("Move my Friday check-in", "h1"), toolMessage(envelope, "t1", "c1", "success")])),
      emptyStream(),
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByText("✓ Confirmed")).toBeInTheDocument();
    expect(screen.getByText("Friday check-in")).toBeInTheDocument();
    expect(screen.getByText("Mon, Aug 25 · 10:00 AM")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm change" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Keep original time" })).toBeNull();
  });

  it("re-renders the declined outcome from a calendar-change envelope", async () => {
    const envelope = JSON.stringify({
      turn_scope_id: "scope-1",
      block_id: "calendar-change:op-2",
      data: { card: { ...CALENDAR_INTERRUPT, status: "declined" }, schedule: {} },
      text: "Change declined.",
    });
    const deps = fakeDeps(
      mountDeps(() => stateWith([toolMessage(envelope, "t2", "c2", "success")])),
      emptyStream(),
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByText("Declined — keeping the original time")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm change" })).toBeNull();
  });

  it("a failed resume (dead thread) shows the error and re-enables the card once", async () => {
    const stream = fakeStream(() =>
      (async function* () {
          throw new Error("Thread not found");
        })(),
    );
    const deps = fakeDeps(mountDeps(() => stateWith([], [{ value: CALENDAR_INTERRUPT }])), stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Confirm change" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Thread not found");
    expect(await screen.findByTestId("interrupt-card")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    expect(stream.calls[1]?.payload).toEqual({ command: { resume: { accept: true } } });
  });
});

describe("MemoryExtractionCard interrupt surface", () => {
  function memoryDeps(stream = emptyStream()) {
    return fakeDeps(
      mountDeps(() => stateWith([], [{ value: { sourceLabel: ".pdf · 182 bytes", fields: MEMORY_FIELDS } }])),
      stream,
    );
  }

  it("renders fields with needsReview tags", async () => {
    render(<ChatShell deps={memoryDeps()} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByTestId("interrupt-card")).toBeInTheDocument();
    expect(screen.getByText("Goal weight")).toBeInTheDocument();
    expect(screen.getByText("Check this")).toBeInTheDocument();
    expect(screen.getByText("Current medications")).toBeInTheDocument();
  });

  it("inline-edited values ride the resume payload {accept:true, fields}", async () => {
    const stream = fakeStream(() => []);
    render(<ChatShell deps={memoryDeps(stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit Goal weight" }));
    const input = screen.getByDisplayValue("180 lb");
    await user.clear(input);
    await user.type(input, "175 lb");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Save to profile" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({
      command: {
        resume: {
          accept: true,
          fields: [
            { key: "goalWeight", value: "175 lb" },
            { key: "medications", value: "Metformin 500mg" },
          ],
        },
      },
    });
  });

  it("discard resumes with exactly {accept:false}", async () => {
    const stream = fakeStream(() => []);
    render(<ChatShell deps={memoryDeps(stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Discard" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({ command: { resume: { accept: false } } });
  });

  it("the confirmation message re-renders per-field saved/discarded state — no buttons, no raw JSON", async () => {
    const confirmation = JSON.stringify({
      component: "MemoryExtractionCard",
      data: {
        sourceLabel: ".pdf · 182 bytes",
        fields: [
          { key: "goalWeight", label: "Goal weight", value: "180 lb", status: "saved" },
          { key: "medications", label: "Current medications", value: "Metformin 500mg", status: "discarded", notice: "Privacy checks failed; field was not saved." },
        ],
      },
    });
    const deps = fakeDeps(
      mountDeps(() => stateWith([humanMessage("Please review this document.", "h1"), aiMessage(confirmation, "a1")])),
      emptyStream(),
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByTestId("memory-confirmation")).toBeInTheDocument();
    expect(screen.getByText("✓ Saved")).toBeInTheDocument();
    expect(screen.getByText("Discarded")).toBeInTheDocument();
    expect(screen.getByText("Privacy checks failed; field was not saved.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save to profile" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
    expect(document.body.textContent ?? "").not.toContain('"component"');
  });

  it("a malformed component payload degrades to nothing — no card, no raw JSON, no crash", async () => {
    const malformed = JSON.stringify({ component: "MemoryExtractionCard", data: { nope: true } });
    const deps = fakeDeps(
      mountDeps(() => stateWith([aiMessage(malformed, "a1")])),
      emptyStream(),
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    await waitFor(() => expect(deps.api.getThreadState).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId("memory-confirmation")).toBeNull();
    expect(document.body.textContent ?? "").not.toContain('"component"');
  });
});

describe("DocumentIngestCard stage progression from event fixtures", () => {
  const info = {
    uploadId: "00000000-0000-4000-8000-000000000001",
    threadId: "11111111-1111-4111-8111-111111111111",
    fileName: "intake-form.pdf",
    fileSizeLabel: "178 KB",
  };

  it("walks uploading → scanning → extracting → done from server stage events only", () => {
    let state: UploadUi = { phase: "idle" };
    const view = (upload: UploadUi) => (
      <MessageList
        turns={[]}
        pendingInterrupt={null}
        upload={upload}
        busy={false}
        onApprove={() => {}}
        latestAiMessageId={null}
      />
    );
    const { rerender } = render(view(state));

    state = applyUploadEvent(state, { kind: "started", info });
    rerender(view(state));
    expect(screen.getByTestId("document-ingest")).toBeInTheDocument();
    expect(screen.getByText("Uploading…")).toBeInTheDocument();

    const expected: Record<string, string> = {
      uploading: "Uploading…",
      scanning: "Scanning document…",
      extracting: "Extracting key details…",
      done: "Ready to review",
    };
    for (const stage of ["uploading", "scanning", "extracting", "done"] as const) {
      state = applyUploadEvent(state, { kind: "stage", stage });
      rerender(view(state));
      expect(screen.getByTestId("document-ingest")).toBeInTheDocument();
      expect(screen.getByText(expected[stage] ?? "")).toBeInTheDocument();
    }
  });
});

describe("ReminderCard full mode (scheduled-delivery surface)", () => {
  function reminderDeps(stream = emptyStream()) {
    return fakeDeps(
      mountDeps(() => stateWith([humanMessage("earlier turn", "h0"), reminderDeliveryMessage("r-1")])),
      stream,
    );
  }

  it("renders the literal bubble + full card, never the envelope JSON", async () => {
    render(<ChatShell deps={reminderDeps()} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByTestId("reminder-card")).toBeInTheDocument();
    expect(screen.getByText("This is your scheduled reminder.")).toBeInTheDocument();
    expect(screen.getByText("Weekly weight log")).toBeInTheDocument();
    expect(screen.getByText("Every Monday at 8:00 AM")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit schedule" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel reminder" })).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toContain("turn_scope_id");
  });

  it("an envelope whose card data fails validation renders the literal only — no card, no JSON", async () => {
    const envelope = JSON.stringify({
      turn_scope_id: "scope-r",
      block_id: "reminder:r-9",
      data: { title: 42 },
      text: "Scheduled reminder.",
    });
    const deps = fakeDeps(
      mountDeps(() => stateWith([aiMessage(`This is your scheduled reminder.\n${envelope}`, "a1")])),
      emptyStream(),
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByText("This is your scheduled reminder.")).toBeInTheDocument();
    expect(screen.queryByTestId("reminder-card")).toBeNull();
    expect(document.body.textContent ?? "").not.toContain("turn_scope_id");
  });

  it("toggle dispatches a NEW chat turn pausing the reminder", async () => {
    const stream = fakeStream(() => []);
    render(<ChatShell deps={reminderDeps(stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Pause reminder" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({
      input: { question: "Pause my Weekly weight log reminder" },
    });
  });

  it("editing the schedule dispatches a move turn with the picked weekday + time", async () => {
    const stream = fakeStream(() => []);
    render(<ChatShell deps={reminderDeps(stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit schedule" }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "Wednesday" } });
    fireEvent.change(screen.getByDisplayValue("08:00"), { target: { value: "07:30" } });
    await user.click(screen.getByRole("button", { name: "Save time" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({
      input: { question: "Move my Weekly weight log reminder to Wednesday at 7:30 AM" },
    });
  });

  it("cancel dispatches a cancel turn", async () => {
    const stream = fakeStream(() => []);
    render(<ChatShell deps={reminderDeps(stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Cancel reminder" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({
      input: { question: "Cancel my Weekly weight log reminder" },
    });
  });
});

describe("ReminderCard compact mode (reminders:list envelope)", () => {
  function listDeps() {
    const envelope = JSON.stringify({
      turn_scope_id: "scope-1",
      block_id: "reminders:list",
      data: {
        items: [
          { reminder_id: "r-1", title: "Weekly weight log", scheduleLabel: "Every Monday at 8:00 AM", active: true },
          { reminder_id: "r-2", title: "Hydration nudge", scheduleLabel: "Every day at 12:00 PM", active: false, nextRun: "Mon, Aug 24" },
        ],
      },
      text: "Your reminders.",
    });
    return fakeDeps(
      mountDeps(() => stateWith([humanMessage("what are my reminders", "h1"), toolMessage(envelope, "t1", "c1", "success")])),
      emptyStream(),
    );
  }

  it("renders read-only rows from the items envelope", async () => {
    render(<ChatShell deps={listDeps()} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByText("Weekly weight log")).toBeInTheDocument();
    expect(screen.getByText("Every Monday at 8:00 AM")).toBeInTheDocument();
    expect(screen.getByText("Hydration nudge")).toBeInTheDocument();
    expect(screen.getByText("Every day at 12:00 PM · Paused")).toBeInTheDocument();
  });

  it("is read-only: the compact toggle sends no turn", async () => {
    const stream = fakeStream(() => []);
    const deps = fakeDeps(
      mountDeps(() => {
        const envelope = JSON.stringify({
          turn_scope_id: "scope-1",
          block_id: "reminders:list",
          data: { items: [{ reminder_id: "r-1", title: "Weekly weight log", scheduleLabel: "Every Monday at 8:00 AM", active: true }] },
          text: "Your reminders.",
        });
        return stateWith([toolMessage(envelope, "t1", "c1", "success")]);
      }),
      stream,
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Pause reminder" }));
    expect(stream.calls).toHaveLength(0);
  });
});

describe("composed-tree rendering in the transcript", () => {
  const trendEnvelope = JSON.stringify({
    turn_scope_id: "scope-1",
    block_id: "trend:weight",
    data: { label: "Weight", value: "182.4", points: [189, 188, 186.5, 185, 184, 183, 182.4] },
    text: "Logged.",
  });

  function composeTreeTree(withButton: boolean) {
    const trend = {
      component: "TrendCard",
      props: {
        label: { __ref: { turn_scope_id: "scope-1", block_id: "trend:weight", pointer: "/label" } },
        value: { __ref: { turn_scope_id: "scope-1", block_id: "trend:weight", pointer: "/value" } },
        points: { __ref: { turn_scope_id: "scope-1", block_id: "trend:weight", pointer: "/points" } },
      },
    };
    const children = withButton
      ? [
          trend,
          { component: "Button", props: { label: "Log it", action: "log_weight" } },
        ]
      : [trend];
    return [
      {
        component: "Card",
        props: { text: "This week" },
        children,
      },
    ];
  }

  function composeMessages(status: string, withButton = false) {
    return [
      humanMessage("show my progress", "h1"),
      aiMessage("", "a1", {
        tool_calls: [{ id: "c1", name: "compose_ui", args: { tree: composeTreeTree(withButton) } }],
      }),
      toolMessage(trendEnvelope, "t1", "c1", status),
    ];
  }

  it("renders the hydrated tree after its correlated successful ToolMessage and dispatches buttons as turns", async () => {
    const stream = fakeStream(() => []);
    const deps = fakeDeps(mountDeps(() => stateWith(composeMessages("success", true))), stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByTestId("compose-tree")).toBeInTheDocument();
    expect(screen.getByText("This week")).toBeInTheDocument();
    expect(screen.getByText("182.4")).toBeInTheDocument();
    expect(document.querySelector("svg polyline")).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Log it" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({ input: { question: "Log today's weight" } });
  });

  it("suppresses the tree when the correlated ToolMessage has error status (plain-text fallback)", async () => {
    const deps = fakeDeps(mountDeps(() => stateWith(composeMessages("error"))), emptyStream());
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    await screen.findByText("show my progress");
    expect(screen.queryByTestId("compose-tree")).toBeNull();
    expect(screen.queryByText("This week")).toBeNull();
  });

  it("a composed confirm Button resolves the pending interrupt with {accept:true}", async () => {
    const stream = fakeStream(() => []);
    const messages = [
      humanMessage("show my progress", "h1"),
      aiMessage("", "a1", {
        tool_calls: [
          {
            id: "c1",
            name: "compose_ui",
            args: { tree: [{ component: "Button", props: { label: "Approve", action: "confirm" } }] },
          },
        ],
      }),
      toolMessage(trendEnvelope, "t1", "c1", "success"),
    ];
    const deps = fakeDeps(
      mountDeps(() => stateWith(messages, [{ value: CALENDAR_INTERRUPT }])),
      stream,
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    const call = stream.calls[0] as StreamCall;
    expect(call.payload).toEqual({ command: { resume: { accept: true } } });
  });

  it("a composed decline Button resolves the pending interrupt with {accept:false}", async () => {
    const stream = fakeStream(() => []);
    const messages = [
      humanMessage("show my progress", "h1"),
      aiMessage("", "a1", {
        tool_calls: [
          {
            id: "c1",
            name: "compose_ui",
            args: { tree: [{ component: "Button", props: { label: "Reject", action: "decline" } }] },
          },
        ],
      }),
      toolMessage(trendEnvelope, "t1", "c1", "success"),
    ];
    const deps = fakeDeps(
      mountDeps(() => stateWith(messages, [{ value: CALENDAR_INTERRUPT }])),
      stream,
    );
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    const call = stream.calls[0] as StreamCall;
    expect(call.payload).toEqual({ command: { resume: { accept: false } } });
  });

  it("a composed confirm Button no-ops without a pending interrupt", async () => {
    const stream = fakeStream(() => []);
    const messages = [
      humanMessage("show my progress", "h1"),
      aiMessage("", "a1", {
        tool_calls: [
          {
            id: "c1",
            name: "compose_ui",
            args: { tree: [{ component: "Button", props: { label: "Approve", action: "confirm" } }] },
          },
        ],
      }),
      toolMessage(trendEnvelope, "t1", "c1", "success"),
    ];
    const deps = fakeDeps(mountDeps(() => stateWith(messages)), stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(stream.calls).toHaveLength(0);
  });

  it("renders the shell but never an unresolved fact", async () => {
    const stale = JSON.stringify({
      turn_scope_id: "scope-1",
      block_id: "trend:bmi",
      data: { label: "BMI", value: "27.1", points: [28, 27.5, 27.1] },
      text: "Logged.",
    });
    const messages = [
      humanMessage("show my progress", "h1"),
      aiMessage("", "a1", {
        tool_calls: [{ id: "c1", name: "compose_ui", args: { tree: composeTreeTree(false) } }],
      }),
      toolMessage(stale, "t1", "c1", "success"),
    ];
    const deps = fakeDeps(mountDeps(() => stateWith(messages)), emptyStream());
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    expect(await screen.findByTestId("compose-tree")).toBeInTheDocument();
    expect(screen.getByText("This week")).toBeInTheDocument();
    expect(screen.queryByText("182.4")).toBeNull();
    expect(screen.queryByText("Weight")).toBeNull();
  });
});
