import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatShell } from "@/chat/components/ChatShell";
import type { CoachApiBundle, CoachChatDeps, CoachStreamBundle } from "@/chat/useCoachChat";
import type { ThreadSummary } from "@/chat/coachApi";
import { ERASE_MARKER_NAME, SENTINEL_QUESTION } from "@/chat/coachProtocol";
import {
  aiMessage,
  emptyStream,
  fakeDeps,
  fakeStream,
  humanMessage,
  interruptPart,
  thread,
  toolMessage,
  updatesPart,
  type StreamCall,
} from "./helpers";

const EMAIL = "member@example.com";

function shell(deps: CoachChatDeps) {
  return render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
}

async function settled() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("openers", () => {
  it("sends a text opener as a new run", async () => {
    const stream = fakeStream(() => [
      updatesPart("rag_relay", [aiMessage("Here you go.", "a1")]),
    ]);
    const deps = fakeDeps({}, stream);
    shell(deps);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Log today's weight" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({
      input: { question: "Log today's weight" },
    });
    expect(await screen.findByText("Here you go.")).toBeInTheDocument();
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
});

describe("new conversation", () => {
  it("clears the transcript and starts fresh (thread created lazily on next send)", async () => {
    const stream = fakeStream(() => [updatesPart("rag_relay", [aiMessage("Logged.", "a1")])]);
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
    const stream = fakeStream(() => [updatesPart("coach_agent", [toolMessage("{}", "t1", "c1", "success")])]);
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
    const stream = fakeStream(() => []);
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
    const stream = fakeStream(() => [updatesPart("finalize_coach", [aiMessage("I found a few details.", "a1")])]);
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
    const stream: CoachStreamBundle & { calls: StreamCall[] } = {
      calls: [],
      streamRun(threadId, payload) {
        stream.calls.push({ threadId, payload });
        return (async function* () {
          await new Promise<void>((resolve) => {
            gate.release = resolve;
          });
          yield updatesPart("rag_relay", [aiMessage("done", "a1")]);
        })();
      },
    };
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
    const stream = fakeStream(() => [
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
