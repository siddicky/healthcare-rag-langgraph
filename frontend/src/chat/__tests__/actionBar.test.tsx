import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatShell } from "@/chat/components/ChatShell";
import type { CoachApiBundle } from "@/chat/useCoachChat";
import type { ThreadSummary } from "@/chat/coachApi";
import {
  aiMessage,
  envelopeString,
  fakeDeps,
  fakeStream,
  humanMessage,
  thread,
  toolMessage,
  updatesPart,
  type StreamCall,
} from "./helpers";

const EMAIL = "member@example.com";

function shellWith(stream: ReturnType<typeof fakeStream>, api: Partial<CoachApiBundle> = {}) {
  return render(<ChatShell deps={fakeDeps(api, stream)} email={EMAIL} onSignedOut={() => {}} />);
}

function regenerateButton() {
  return screen.queryByRole("button", { name: "Regenerate" });
}

async function sendAndSettle(user: ReturnType<typeof userEvent.setup>, stream: ReturnType<typeof fakeStream>) {
  await user.click(screen.getByRole("button", { name: "Set a weekly weigh-in reminder" }));
  await waitFor(() => expect(stream.calls).toHaveLength(1));
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("action bar — regenerate", () => {
  it("waits for run terminality (status poll) then re-sends the turn window's question", async () => {
    const statusCalls: string[] = [];
    let releaseBusyPoll: (() => void) | null = null;
    const getThread = vi.fn((id: string): Promise<ThreadSummary> => {
      statusCalls.push(id);
      if (statusCalls.length === 1) {
        return new Promise((resolve) => {
          releaseBusyPoll = () => resolve(thread(id, { status: "busy" }));
        });
      }
      return Promise.resolve(thread(id, { status: "idle" }));
    });
    const stream = fakeStream((_call, index) =>
      index === 0
        ? [updatesPart("rag_relay", [aiMessage("A protein-forward breakfast.", "a1")])]
        : [updatesPart("rag_relay", [aiMessage("Regenerated answer.", "a2")])],
    );
    shellWith(stream, { getThread });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "What is a healthy breakfast?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("A protein-forward breakfast.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(getThread).toHaveBeenCalledTimes(1);
    expect(stream.calls).toHaveLength(1);

    (releaseBusyPoll ?? (() => {}))();
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    const reSent = stream.calls[1] as StreamCall;
    expect(reSent.payload).toEqual({ input: { question: "What is a healthy breakfast?" } });
    expect(await screen.findByText("Regenerated answer.")).toBeInTheDocument();
  });

  it("is ABSENT for every mutating tool family in the latest turn", async () => {
    for (const name of ["log_metric", "log_injection", "change_schedule", "remember_fact", "create_reminder", "edit_reminder", "cancel_reminder"]) {
      const stream = fakeStream(() => [
        updatesPart("coach_agent", [
          aiMessage("", "a1", { tool_calls: [{ id: "c1", name, args: {} }] }),
          toolMessage(envelopeString("trend:weight", { value: "1" }), "t1", "c1"),
          aiMessage("Done — logged it.", "a2"),
        ]),
      ]);
      const { unmount } = render(<ChatShell deps={fakeDeps({}, stream)} email={EMAIL} onSignedOut={() => {}} />);
      const user = userEvent.setup();
      await sendAndSettle(user, stream);
      expect(await screen.findByText("Done — logged it.")).toBeInTheDocument();
      expect(regenerateButton(), name).toBeNull();
      unmount();
    }
  });

  it("is ABSENT in the older-safe-turn-then-mutation case (the safe turn cannot replay past the mutation)", async () => {
    const stream = fakeStream((_call, index) =>
      index === 0
        ? [updatesPart("rag_relay", [aiMessage("older safe answer", "a0")])]
        : [
            updatesPart("coach_agent", [
              aiMessage("", "a1", { tool_calls: [{ id: "c1", name: "log_metric", args: {} }] }),
              toolMessage(envelopeString("trend:weight", { value: "182" }), "t1", "c1"),
            ]),
          ],
    );
    shellWith(stream);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "safe question");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("older safe answer");
    expect(regenerateButton()).not.toBeNull();

    await user.type(screen.getByLabelText("Message your coach"), "log my weight at 182");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    await waitFor(() => expect(regenerateButton()).toBeNull());
  });

  it("is ABSENT while an interrupt is pending or the latest window is a document turn", async () => {
    const threads: ThreadSummary[] = [thread("t-1")];
    const interruptApi: Partial<CoachApiBundle> = {
      searchThreads: vi.fn(async () => threads),
      getThreadState: vi.fn(async () => ({
        values: { messages: [humanMessage("hi", "h1"), aiMessage("hello", "a1")] },
        interrupts: [{ value: { eventLabel: "X", fromLabel: "A", toLabel: "B" } }],
      })),
    };
    const stream = fakeStream(() => []);
    render(<ChatShell deps={fakeDeps(interruptApi, stream)} email={EMAIL} onSignedOut={() => {}} />);
    await screen.findByText("hello");
    expect(regenerateButton()).toBeNull();
  });
});

describe("action bar — branch", () => {
  it("calls the own-thread LATEST-state copy (bodyless) and switches to the new thread", async () => {
    const copied = thread("t-copy");
    const copyThread = vi.fn(async () => copied);
    const getThreadState = vi.fn(async (id: string) =>
      id === "t-copy"
        ? { values: { messages: [humanMessage("same question", "h1"), aiMessage("same answer", "a1")] }, interrupts: [] }
        : { values: { messages: [humanMessage("same question", "h1"), aiMessage("same answer", "a1")] }, interrupts: [] },
    );
    const threads: ThreadSummary[] = [thread("t-1")];
    const searchThreads = vi.fn(async () => threads);
    const stream = fakeStream(() => [updatesPart("rag_relay", [aiMessage("same answer", "a1")])]);
    render(
      <ChatShell deps={fakeDeps({ copyThread, getThreadState, searchThreads }, stream)} email={EMAIL} onSignedOut={() => {}} />,
    );
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "same question");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("same answer");

    await user.click(screen.getByRole("button", { name: "Branch into a new thread" }));
    await waitFor(() => expect(copyThread).toHaveBeenCalledWith("t-1"));
    await waitFor(() => expect(getThreadState).toHaveBeenCalledWith("t-copy"));
    expect(await screen.findByText("same answer")).toBeInTheDocument();
  });
});

describe("action bar — thumbs feedback", () => {
  it("posts the proxy shape {thread_id, message_id, score}", async () => {
    const postFeedback = vi.fn(async () => undefined);
    const stream = fakeStream(() => [updatesPart("rag_relay", [aiMessage("Nice work.", "a1")])]);
    render(<ChatShell deps={fakeDeps({ postFeedback }, stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await sendAndSettle(user, stream);
    await screen.findByText("Nice work.");
    await user.click(screen.getByRole("button", { name: "Good response" }));
    await waitFor(() => expect(postFeedback).toHaveBeenCalledTimes(1));
    expect(postFeedback).toHaveBeenCalledWith({
      threadId: "11111111-1111-4111-8111-111111111111",
      messageId: "a1",
      score: 1,
    });
    expect(screen.getByRole("button", { name: "Good response" })).toBeDisabled();
  });

  it("down-votes with score -1", async () => {
    const postFeedback = vi.fn(async () => undefined);
    const stream = fakeStream(() => [updatesPart("rag_relay", [aiMessage("Nice work.", "a1")])]);
    render(<ChatShell deps={fakeDeps({ postFeedback }, stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await sendAndSettle(user, stream);
    await screen.findByText("Nice work.");
    await user.click(screen.getByRole("button", { name: "Bad response" }));
    await waitFor(() => expect(postFeedback).toHaveBeenCalledWith({ threadId: expect.any(String), messageId: "a1", score: -1 }));
  });

  it("no-ops and disables after a feedback failure", async () => {
    const postFeedback = vi.fn(async () => {
      throw new Error("feedback down");
    });
    const stream = fakeStream(() => [updatesPart("rag_relay", [aiMessage("Nice work.", "a1")])]);
    render(<ChatShell deps={fakeDeps({ postFeedback }, stream)} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await sendAndSettle(user, stream);
    await screen.findByText("Nice work.");
    await user.click(screen.getByRole("button", { name: "Good response" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Good response" })).toBeDisabled());
    const calls = postFeedback.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Good response" }).closest("div") ?? document.body);
    expect(postFeedback.mock.calls.length).toBe(calls);
  });
});
