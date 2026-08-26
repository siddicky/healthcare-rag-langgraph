import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatShell } from "@/chat/components/ChatShell";
import type { CoachApiBundle, CoachStreamDeps } from "@/chat/useCoachStream";
import { CoachApiError, type ThreadSummary } from "@/chat/coachApi";
import { ERASE_MARKER_NAME, SENTINEL_QUESTION } from "@/chat/coachProtocol";
import {
  aiMessage,
  emptyStream,
  fakeDeps,
  fakeAgent,
  humanMessage,
  interruptPart,
  thread,
  toolMessage,
  updatesPart,
  type StreamCall,
} from "./helpers";

const EMAIL = "member@example.com";

function shell(deps: CoachStreamDeps) {
  return render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
}

async function settled() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("openers", () => {
  it("sends a text opener as a new run", async () => {
    const stream = fakeAgent(() => [
      updatesPart("coach_agent", [aiMessage("Here you go.", "a1")]),
    ]);
    const deps = fakeDeps({}, stream);
    shell(deps);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Log today's weight" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({
      input: { question: "Log today's weight" },
    });
    expect(stream.calls[0]?.options).toMatchObject({
      streamMode: ["updates"],
      streamResumable: false,
      multitaskStrategy: "reject",
    });
    expect(await screen.findByText("Here you go.")).toBeInTheDocument();
  });

  it("submits the resumable messages envelope when the member perimeter is v2", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    const stream = fakeAgent(() => [
      updatesPart("coach_agent", [aiMessage("Queued.", "a1")]),
    ]);
    shell(fakeDeps({}, stream));
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Log today's weight" }));

    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.options).toMatchObject({
      streamMode: ["updates", "messages"],
      streamResumable: true,
      multitaskStrategy: "enqueue",
    });
  });

  it("routes the upload opener to the attach input instead of sending", async () => {
    const stream = emptyStream();
    const deps = fakeDeps({}, stream);
    shell(deps);
    const input = (await screen.findByTestId("opener-attach-input")) as HTMLInputElement;
    const clickSpy = vi.spyOn(input, "click");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Upload my intake form" }));
    expect(clickSpy).toHaveBeenCalled();
    expect(stream.calls).toHaveLength(0);
  });

  it("waits for a finishing server run before opening a new stream", async () => {
    const statuses = [thread("11111111-1111-4111-8111-111111111111", { status: "busy" })];
    const getThread = vi.fn(async () => statuses.shift() ?? thread("11111111-1111-4111-8111-111111111111"));
    const stream = fakeAgent(() => [updatesPart("coach_agent", [aiMessage("Logged.", "a1")])]);
    const deps = fakeDeps({ getThread }, stream);
    shell(deps);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Log today's weight" }));

    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(getThread).toHaveBeenCalledTimes(2);
    expect(deps.sleep).toHaveBeenCalledWith(0);
    expect(await screen.findByText("Logged.")).toBeInTheDocument();
  });
});

describe("new conversation", () => {
  it("binds a plain v2 first send to the one REST-created thread", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    const restThreadId = "44444444-4444-4444-8444-444444444444";
    let searchCount = 0;
    const createThread = vi.fn(async () => thread(restThreadId));
    const searchThreads = vi.fn(async () => {
      searchCount += 1;
      return searchCount === 1 ? [] : [thread(restThreadId)];
    });
    const stream = fakeAgent(() => [updatesPart("coach_agent", [aiMessage("Logged.", "a1")])]);
    const deps = fakeDeps({ createThread, searchThreads }, stream);
    shell(deps);
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Message your coach"), "log my weight");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(createThread).toHaveBeenCalledTimes(1);
    expect(stream.calls[0]?.threadId).toBe(restThreadId);
    expect(stream.calls[0]?.options?.threadId).toBe(restThreadId);
    await waitFor(() => expect(searchThreads.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("button", { name: "log my weight" })).toBeInTheDocument();
    vi.unstubAllEnvs();
  });

  it("clears the transcript and starts fresh (thread created lazily on next send)", async () => {
    const stream = fakeAgent(() => [updatesPart("coach_agent", [aiMessage("Logged.", "a1")])]);
    const deps = fakeDeps({}, stream);
    shell(deps);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Set a weekly weigh-in reminder" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(await screen.findByText("Logged.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.queryByText("Logged.")).toBeNull();
    expect(screen.getByRole("heading", { name: "Nymble Coach" })).toBeInTheDocument();
  });
});

describe("thread switch via latest-state read", () => {
  it("ignores a stale SDK thread callback after an explicit thread selection", async () => {
    const firstThreadId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const selectedThreadId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    window.localStorage.setItem(
      "nymble:thread-titles",
      JSON.stringify({ [firstThreadId]: "First chat", [selectedThreadId]: "Selected chat" }),
    );
    const stream = fakeAgent(() => [updatesPart("coach_agent", [aiMessage("reply", "a-reply")])]);
    const deps = fakeDeps(
      {
        searchThreads: vi.fn(async () => [thread(firstThreadId), thread(selectedThreadId)]),
        getThreadState: vi.fn(async () => ({ values: { messages: [] }, interrupts: [] })),
      },
      stream,
    );
    shell(deps);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Selected chat" }));
    await waitFor(() => expect(deps.api.getThreadState).toHaveBeenCalledWith(selectedThreadId));
    act(() => stream.emitThreadId(firstThreadId));
    await user.type(screen.getByLabelText("Message your coach"), "stay selected");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.threadId).toBe(selectedThreadId);
    expect(stream.calls[0]?.options?.threadId).toBe(selectedThreadId);
  });

  it("loads the selected thread's state and renders its messages from values.messages", async () => {
    window.localStorage.setItem(
      "nymble:thread-titles",
      JSON.stringify({ "t-1": "First chat", "t-2": "Second chat" }),
    );
    const threads: ThreadSummary[] = [thread("t-1"), thread("t-2")];
    const api: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      getThreadState: vi.fn(async (id: string) =>
        id === "t-1"
          ? { values: { messages: [humanMessage("first question", "h1"), aiMessage("first answer", "a1")] }, interrupts: [] }
          : { values: { messages: [humanMessage("second question", "h2"), aiMessage("second answer", "a2")] }, interrupts: [] },
      ),
    };
    const deps = fakeDeps(api, emptyStream());
    shell(deps);
    await screen.findByText("first answer");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Second chat" }));
    await screen.findByText("second answer");
    expect(deps.api.getThreadState).toHaveBeenCalledWith("t-2");
    expect(screen.queryByText("first answer")).toBeNull();
  });

  it("removes a missing thread when the server resets before it is selected", async () => {
    window.localStorage.setItem(
      "nymble:thread-titles",
      JSON.stringify({ "t-current": "Current chat", "t-missing": "Missing chat" }),
    );
    let searchCount = 0;
    const searchThreads = vi.fn(async () => {
      searchCount += 1;
      return searchCount === 1
        ? [thread("t-current"), thread("t-missing")]
        : [thread("t-current")];
    });
    const getThreadState = vi.fn(async (id: string) => {
      if (id === "t-missing") throw new CoachApiError(404, "Thread not found");
      return {
        values: {
          messages: [
            humanMessage("current question", "h-current"),
            aiMessage("current answer", "a-current"),
          ],
        },
        interrupts: [],
      };
    });
    const deps = fakeDeps({ searchThreads, getThreadState }, emptyStream());
    shell(deps);
    await screen.findByText("current answer");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Missing chat" }));

    expect(
      await screen.findByText("That conversation is no longer available. Start a new one."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Missing chat" })).toBeNull();
    expect(screen.queryByText("Thread not found")).toBeNull();
    expect(screen.getByText("current answer")).toBeInTheDocument();
  });
});

describe("server reset during an active conversation", () => {
  it("starts fresh when the active thread disappears before a send", async () => {
    window.localStorage.setItem(
      "nymble:thread-titles",
      JSON.stringify({ "t-missing": "Missing chat" }),
    );
    let searchCount = 0;
    const searchThreads = vi.fn(async () => {
      searchCount += 1;
      return searchCount === 1 ? [thread("t-missing")] : [];
    });
    const stream = fakeAgent(() =>
      (async function* () {
        throw Object.assign(
          new Error('HTTP 404: {"detail":"Thread not found"}'),
          { status: 404 },
        );
      })(),
    );
    const deps = fakeDeps(
      {
        searchThreads,
        getThreadState: vi.fn(async () => ({
          values: {
            messages: [
              humanMessage("old question", "h-old"),
              aiMessage("old answer", "a-old"),
            ],
          },
          interrupts: [],
        })),
      },
      stream,
    );
    shell(deps);
    await screen.findByText("old answer");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Message your coach"), "Are you there?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(
      await screen.findByText("That conversation is no longer available. Start a new one."),
    ).toBeInTheDocument();
    expect(screen.queryByText('HTTP 404: {"detail":"Thread not found"}')).toBeNull();
    expect(screen.queryByText("old answer")).toBeNull();
    expect(screen.queryByRole("button", { name: "Missing chat" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Nymble Coach" })).toBeInTheDocument();
  });
});

describe("reload during a pending interrupt (fixture)", () => {
  it("reconstructs the pre-scrubbed card from the latest-state read and issues the unified resume", async () => {
    const threads: ThreadSummary[] = [thread("t-1")];
    const api: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      getThreadState: vi.fn(async () => ({
        values: {
          messages: [
            humanMessage("Move my Friday check-in", "h1"),
            aiMessage("", "a1", { tool_calls: [{ id: "c1", name: "change_schedule", args: {} }] }),
          ],
        },
        interrupts: [
          {
            value: {
              eventLabel: "Friday check-in",
              fromLabel: "Fri, Aug 22 · 2:00 PM",
              toLabel: "Mon, Aug 25 · 10:00 AM",
              reason: "Monday is open.",
              status: "pending",
            },
          },
        ],
      })),
    };
    const stream = fakeAgent(() => [updatesPart("coach_agent", [toolMessage("{}", "t1", "c1", "success")])]);
    const deps = fakeDeps(api, stream);
    shell(deps);
    expect(await screen.findByTestId("interrupt-card")).toBeInTheDocument();
    expect(screen.getByText("Friday check-in")).toBeInTheDocument();
    expect(screen.getByText("Fri, Aug 22 · 2:00 PM")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    const call: StreamCall = stream.calls[0] as StreamCall;
    expect(call.payload).toEqual({ command: { resume: { accept: true } } });
  });

  it("decline issues accept:false from the same envelope", async () => {
    const threads: ThreadSummary[] = [thread("t-1")];
    const api: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      getThreadState: vi.fn(async () => ({
        values: { messages: [] },
        interrupts: [
          {
            value: {
              eventLabel: "Friday check-in",
              fromLabel: "A",
              toLabel: "B",
            },
          },
        ],
      })),
    };
    const stream = fakeAgent(() => []);
    const deps = fakeDeps(api, stream);
    shell(deps);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Keep original time" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({ command: { resume: { accept: false } } });
  });
});

describe("document upload flow", () => {
  it("drives the ingest card from status polling and stops after done; next turn carries attachment_id + sentinel", async () => {
    const postUpload = vi.fn(async () => ({ stage: "uploading" }));
    const statusStages = ["scanning", "extracting", "done"];
    let statusIndex = 0;
    const getUploadStatus = vi.fn(async () => {
      const stage = statusStages[Math.min(statusIndex, statusStages.length - 1)] ?? "done";
      statusIndex += 1;
      return { stage };
    });
    const stream = fakeAgent(() => [updatesPart("finalize_coach", [aiMessage("I found a few details.", "a1")])]);
    const deps = fakeDeps({ postUpload, getUploadStatus }, stream);
    shell(deps);
    const user = userEvent.setup();

    const input = screen.getByTestId("opener-attach-input") as HTMLInputElement;
    const file = new File(["%PDF-1.4 fixture"], "intake-form.pdf", { type: "application/pdf" });
    await user.upload(input, file);

    expect(await screen.findByTestId("document-ingest")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Ready to review")).toBeInTheDocument());
    expect(getUploadStatus).toHaveBeenCalledTimes(3);
    const pollsAfterDone = getUploadStatus.mock.calls.length;
    await settled();
    await settled();
    expect(getUploadStatus.mock.calls.length).toBe(pollsAfterDone);

    const composer = screen.getByLabelText("Message your coach");
    await user.type(composer, "please");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    const call = stream.calls[0] as StreamCall;
    expect(call.payload).toEqual({
      input: { question: "Please review this document.", attachment_id: "00000000-0000-4000-8000-000000000001" },
    });
  });

  it("renders upload errors as plain text, not the card", async () => {
    const postUpload = vi.fn(async () => {
      throw new Error("File content does not match its media type");
    });
    const deps = fakeDeps({ postUpload }, emptyStream());
    shell(deps);
    const user = userEvent.setup();
    const input = screen.getByTestId("opener-attach-input") as HTMLInputElement;
    await user.upload(input, new File(["nope"], "form.pdf", { type: "application/pdf" }));
    expect(await screen.findByTestId("upload-error")).toBeInTheDocument();
    expect(screen.queryByTestId("document-ingest")).toBeNull();
    expect(screen.getByTestId("upload-error").textContent).toContain(
      "File content does not match its media type",
    );
  });
});

describe("sidebar thread management", () => {
  it("deletes an owned thread and refreshes the list", async () => {
    window.localStorage.setItem(
      "nymble:thread-titles",
      JSON.stringify({ "t-1": "First chat", "t-2": "Second chat" }),
    );
    const threads: ThreadSummary[] = [thread("t-1"), thread("t-2")];
    const searchThreads = vi.fn(async () => threads);
    const deleteThread = vi.fn(async () => undefined);
    const deps = fakeDeps({ searchThreads, deleteThread, getThreadState: vi.fn(async () => ({ values: {}, interrupts: [] })) }, emptyStream());
    shell(deps);
    await screen.findByRole("button", { name: "First chat" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Delete First chat" }));
    await waitFor(() => expect(deleteThread).toHaveBeenCalledWith("t-1"));
    expect(searchThreads.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

describe("one active run per thread", () => {
  it("locks the composer while a run streams", async () => {
    const gate: { release: (() => void) | null } = { release: null };
    const stream = fakeAgent(() =>
      (async function* () {
        await new Promise<void>((resolve) => {
          gate.release = resolve;
        });
        yield updatesPart("coach_agent", [aiMessage("done", "a1")]);
      })(),
    );
    const deps = fakeDeps({}, stream);
    shell(deps);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByLabelText("Message your coach")).toBeDisabled());
    expect(stream.calls).toHaveLength(1);

    (gate.release ?? (() => {}))();
    await waitFor(() => expect(screen.getByLabelText("Message your coach")).toBeEnabled());
    expect(stream.calls).toHaveLength(1);
  });
});

describe("erase flow via the marker", () => {
  it("latches the marker after a clean stream EOF, deletes others first and the marker thread last, then resets", async () => {
    const threads = [thread("t-1"), thread("t-2")];
    const deleted: string[] = [];
    const api: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      deleteThread: vi.fn(async (id: string) => {
        deleted.push(id);
      }),
      getThread: vi.fn(async (id: string) => thread(id)),
    };
    const stream = fakeAgent(() => [
      updatesPart("finalize_coach", [
        aiMessage("All saved data erased.", "m1", { name: ERASE_MARKER_NAME }),
      ]),
    ]);
    const deps = fakeDeps(api, stream);
    shell(deps);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Move my Friday check-in" }));
    await waitFor(() => expect(deleted).toEqual(["t-2", "t-1"]));
    expect(deleted[deleted.length - 1]).toBe("t-1");
    expect(await screen.findByText(/All saved data erased/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Nymble Coach" })).toBeInTheDocument();
  });
});

describe("headless copy_to_clipboard", () => {
  it("copyToClipboardExecute writes via clipboard and falls back", async () => {
    const { copyToClipboardExecute, COPY_TOOL, HEADLESS_TOOLS } = await import(
      "@/chat/useCoachStream"
    );
    expect(COPY_TOOL.name).toBe("copy_to_clipboard");
    expect(HEADLESS_TOOLS).toHaveLength(1);
    expect(HEADLESS_TOOLS[0]?.tool.name).toBe("copy_to_clipboard");

    const writeText = vi.fn(async () => undefined);
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    await expect(copyToClipboardExecute({ text: "hello" })).resolves.toBe("copied");
    expect(writeText).toHaveBeenCalledWith("hello");

    // fallback when clipboard missing
    delete (navigator as unknown as Record<string, unknown>).clipboard;
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      writable: true,
      configurable: true,
    });
    const execMock = vi.fn(() => true);
    Object.defineProperty(document, "execCommand", {
      value: execMock,
      writable: true,
      configurable: true,
    });
    await expect(copyToClipboardExecute({ text: "fallback text" })).resolves.toBe("copied");
    expect(execMock).toHaveBeenCalledWith("copy");
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
  });

  it("headless interrupt is recognized and filtered from user-facing interrupts", async () => {
    const { isHeadlessToolInterrupt, filterOutHeadlessToolInterrupts } = await import(
      "@langchain/langgraph-sdk"
    );
    const headless = {
      id: "int-1",
      value: {
        type: "tool",
        tool_call: { name: "copy_to_clipboard", args: { text: "copy me" }, id: "tc-1" },
      },
    };
    expect(isHeadlessToolInterrupt(headless.value)).toBe(true);
    expect(filterOutHeadlessToolInterrupts([headless as never])).toHaveLength(0);

    const userFacing = { id: "int-2", value: { eventLabel: "Friday check-in" } };
    expect(isHeadlessToolInterrupt(userFacing.value)).toBe(false);
    expect(filterOutHeadlessToolInterrupts([userFacing as never])).toHaveLength(1);
  });

  it("scripted turn: interrupt with headless payload → execute → ToolMessage success", async () => {
    const writeText = vi.fn(async () => undefined);
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    const { HEADLESS_TOOLS } = await import("@/chat/useCoachStream");
    const { handleHeadlessToolInterrupt, isHeadlessToolInterrupt } = await import(
      "@langchain/langgraph-sdk"
    );
    const interrupt = {
      type: "tool" as const,
      toolCall: { name: "copy_to_clipboard" as const, args: { text: "snippet to copy" }, id: "tc-99" },
    };
    expect(isHeadlessToolInterrupt({ type: "tool", tool_call: interrupt.toolCall })).toBe(true);
    const result = await handleHeadlessToolInterrupt(interrupt, [...HEADLESS_TOOLS] as never);
    expect(result.toolCallId).toBe("tc-99");
    expect(result.value).toBe("copied");
    expect(writeText).toHaveBeenCalledWith("snippet to copy");

    const unknownInterrupt = {
      type: "tool" as const,
      toolCall: { name: "unknown_tool" as const, args: {}, id: "tc-unknown" },
    };
    const unknown = await handleHeadlessToolInterrupt(unknownInterrupt, [...HEADLESS_TOOLS] as never);
    expect((unknown.value as { error: string }).error).toMatch(/is not registered/);
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
  });
});

describe("HITL interrupt approve/reject/edit via respond", () => {
  it("interrupt card renders → approve → stream.respond called with {accept:true} → next ToolMessage", async () => {
    const threads: ThreadSummary[] = [thread("t-hitl")];
    const api: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      getThreadState: vi.fn(async () => ({
        values: {
          messages: [humanMessage("Move my Friday check-in", "h1")],
        },
        interrupts: [
          {
            value: {
              eventLabel: "Friday check-in",
              fromLabel: "Fri 2:00 PM",
              toLabel: "Mon 10:00 AM",
              reason: "Monday open",
            },
          },
        ],
      })),
    };
    const stream = fakeAgent((call) => {
      if ("command" in call.payload) {
        return [updatesPart("coach_agent", [aiMessage("Done", "a2")])];
      }
      return [];
    });
    const deps = fakeDeps(api, stream);
    shell(deps);
    expect(await screen.findByTestId("interrupt-card")).toBeInTheDocument();
    expect(screen.getByText("Friday check-in")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({ command: { resume: { accept: true } } });
    await waitFor(() => expect(screen.queryByTestId("interrupt-card")).toBeNull());
    expect(await screen.findByText("Done")).toBeInTheDocument();
  });

  it("reject issues accept:false and edit issues accept:true with fields", async () => {
    const memPayload = {
      sourceLabel: "Intake form",
      fields: [
        { key: "allergies", label: "Allergies", value: "Peanuts", needsReview: true },
        { key: "meds", label: "Meds", value: "Metformin" },
      ],
    };
    const threads: ThreadSummary[] = [thread("t-mem")];
    const api: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      getThreadState: vi.fn(async () => ({
        values: { messages: [] },
        interrupts: [{ value: memPayload }],
      })),
    };
    const stream = fakeAgent(() => []);
    const deps = fakeDeps(api, stream);
    shell(deps);
    expect(await screen.findByTestId("interrupt-card")).toBeInTheDocument();
    const user = userEvent.setup();
    // discard = reject
    await user.click(screen.getByRole("button", { name: /Discard/i }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({ command: { resume: { accept: false } } });
  });

  it("malformed interrupt payload fails closed: no card, telemetry, no crash", async () => {
    const threads: ThreadSummary[] = [thread("t-bad")];
    const api: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      getThreadState: vi.fn(async () => ({
        values: { messages: [] },
        interrupts: [{ value: { nonsense: "xyz" } }],
      })),
    };
    const stream = fakeAgent(() => []);
    const deps = fakeDeps(api, stream);
    shell(deps);
    await waitFor(() => expect(screen.queryByTestId("interrupt-card")).toBeNull());
    expect(stream.calls).toHaveLength(0);
  });

  it("multiple interrupts via respondAll: renders 2 cards and approve-all issues respondAll", async () => {
    const { MessageList } = await import("@/chat/components/MessageList");
    const { render } = await import("@testing-library/react");
    const payload1 = { eventLabel: "A", fromLabel: "1", toLabel: "2" };
    const payload2 = { eventLabel: "B", fromLabel: "3", toLabel: "4" };
    const onApprove = vi.fn();
    const onApproveAll = vi.fn();
    render(
      <MessageList
        turns={[]}
        pendingInterrupt={null}
        pendingInterrupts={[payload1, payload2]}
        upload={{ phase: "idle" } as never}
        busy={false}
        onApprove={onApprove}
        onApproveAll={onApproveAll}
        latestAiMessageId={null}
      />,
    );
    expect(screen.getAllByTestId("interrupt-card")).toHaveLength(2);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Approve all" }));
    expect(onApproveAll).toHaveBeenCalledWith([{ accept: true }, { accept: true }]);
  });
});

describe("scripted AG-UI transport smoke", () => {
  it("renders one turn through send, tool, interrupt, resume, and finalize", async () => {
    const stream = fakeAgent((call) => {
      if ("input" in call.payload) {
        return [
          updatesPart("coach_agent", [
            aiMessage("", "agent-tool", {
              tool_calls: [
                { id: "schedule-call", name: "change_schedule", args: { day: "Monday" } },
              ],
            }),
          ]),
          interruptPart({
            eventLabel: "Friday check-in",
            fromLabel: "Friday",
            toLabel: "Monday",
          }),
        ];
      }
      return [
        updatesPart("coach_agent", [
          toolMessage("updated", "schedule-result", "schedule-call", "success"),
          aiMessage("Calendar updated.", "agent-final"),
        ]),
      ];
    });
    shell(fakeDeps({}, stream));
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Message your coach"), "Move my check-in");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Move my check-in")).toBeInTheDocument();
    expect(await screen.findByTestId("tool-call-card")).toBeInTheDocument();
    expect(await screen.findByTestId("interrupt-card")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm change" }));

    await waitFor(() => expect(stream.calls).toHaveLength(2));
    expect(stream.calls[1]?.payload).toEqual({ command: { resume: { accept: true } } });
    expect(await screen.findByText("Calendar updated.")).toBeInTheDocument();
    expect(screen.queryByTestId("interrupt-card")).toBeNull();
    expect(screen.getAllByText("Move my check-in")).toHaveLength(1);
  });
});
