import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatShell } from "@/chat/components/ChatShell";
import { CoachApiError } from "@/chat/coachApi";
import {
  aiMessage,
  fakeDeps,
  fakeStream,
  humanMessage,
  thread,
  updatesPart,
  type StreamCall,
} from "./helpers";

const EMAIL = "member@example.com";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("branching chat — copy branch", () => {
  it("scripted copy branch → new thread id at same state", async () => {
    const copied = thread("t-copy");
    const copyThread = vi.fn(async () => copied);
    const getThreadState = vi.fn(async (id: string) =>
      id === "t-copy"
        ? { values: { messages: [humanMessage("same question", "h1"), aiMessage("same answer", "a1")] }, interrupts: [] }
        : { values: { messages: [humanMessage("same question", "h1"), aiMessage("same answer", "a1")] }, interrupts: [] },
    );
    const threads = [thread("t-1")];
    const searchThreads = vi.fn(async () => threads);
    const stream = fakeStream(() => [updatesPart("coach_agent", [aiMessage("same answer", "a1")])]);
    render(<ChatShell deps={fakeDeps({ copyThread, getThreadState, searchThreads }, stream)} email={EMAIL} onSignedOut={() => {}} />);
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

describe("branching chat — forkFrom via checkpoint", () => {
  it("regenerate resubmits same question via forkFrom when v2 and history available", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    const getThreadHistory = vi.fn(async () => [
      { checkpoint_id: "cp-latest", parent_checkpoint_id: "cp-parent-123", values: { messages: [] } },
    ]);
    const stream = fakeStream((call) => {
      if (call.options?.forkFrom === "cp-parent-123") {
        return [updatesPart("coach_agent", [aiMessage("Regenerated via fork.", "a2")])];
      }
      return [updatesPart("coach_agent", [aiMessage("Original answer.", "a1")])];
    });
    const deps = fakeDeps({ getThreadHistory }, stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "What is a healthy breakfast?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Original answer.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    const regenCall = stream.calls[1] as StreamCall;
    expect(regenCall.options?.forkFrom).toBe("cp-parent-123");
    expect(regenCall.payload).toEqual({ input: { question: "What is a healthy breakfast?" } });
    expect(await screen.findByText("Regenerated via fork.")).toBeInTheDocument();
    expect(getThreadHistory).toHaveBeenCalled();
  });

  it("fork with bad checkpoint surfaces error and does not fallback to send", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    const getThreadHistory = vi.fn(async () => [
      { checkpoint_id: "cp-bad", parent_checkpoint_id: "bad-checkpoint-id", values: { messages: [] } },
    ]);
    const stream = fakeStream((call) => {
      if (call.options?.forkFrom === "bad-checkpoint-id") {
        return (async function* () {
          throw new CoachApiError(404, "Checkpoint not found");
        })();
      }
      return [updatesPart("coach_agent", [aiMessage("Original answer.", "a1")])];
    });
    const deps = fakeDeps({ getThreadHistory }, stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "hello hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Original answer.");
    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument(), { timeout: 3000 });
    expect(stream.calls).toHaveLength(2);
  });

  it("edit → forkFrom resubmits new text for that turn", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    const getThreadHistory = vi.fn(async () => [
      { checkpoint_id: "cp-edit-latest", parent_checkpoint_id: "cp-edit-parent", values: { messages: [] } },
    ]);
    const stream = fakeStream((call) => {
      if (call.options?.forkFrom === "cp-edit-parent" && (call.payload as { input: { question: string } }).input.question === "edited question") {
        return [updatesPart("coach_agent", [aiMessage("Edited answer.", "a2")])];
      }
      return [updatesPart("coach_agent", [aiMessage("Original answer.", "a1")])];
    });
    const deps = fakeDeps({ getThreadHistory }, stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "original question");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Original answer.");
    const editBtn = await screen.findByTestId("turn-edit-btn");
    await user.click(editBtn);
    const input = await screen.findByTestId("turn-edit-input");
    await user.clear(input);
    await user.type(input, "edited question");
    await user.click(screen.getByTestId("turn-edit-submit"));
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    const editCall = stream.calls[1] as StreamCall;
    expect(editCall.options?.forkFrom).toBe("cp-edit-parent");
    expect(editCall.payload).toEqual({ input: { question: "edited question" } });
    expect(await screen.findByText("Edited answer.")).toBeInTheDocument();
  });

  it("fork is gated behind v2 — without v2, regenerate falls back to plain send without forkFrom", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v1");
    const getThreadHistory = vi.fn(async () => [
      { checkpoint_id: "cp1", parent_checkpoint_id: "cp-parent", values: { messages: [] } },
    ]);
    const stream = fakeStream(() => [updatesPart("coach_agent", [aiMessage("Answer.", "a1")])]);
    const deps = fakeDeps({ getThreadHistory }, stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "fallback question");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Answer.");
    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    expect(stream.calls[1]?.options?.forkFrom).toBeUndefined();
    expect(getThreadHistory).not.toHaveBeenCalled();
  });
});
