import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatShell } from "@/chat/components/ChatShell";
import { QueueBar } from "@/chat/components/QueueBar";
import { fakeDeps, fakeStream, thread } from "./helpers";
import { aiMessage, updatesPart } from "./helpers";
import type { QueuedEntry } from "@/chat/useCoachStream";
import { useCoachStream, type CoachStreamDeps } from "@/chat/useCoachStream";
import { useEffect } from "react";

describe("QueueBar", () => {
  it("renders count and entries with design tokens only", () => {
    const queue: QueuedEntry[] = [
      { id: "q1", input: { question: "first" }, createdAt: new Date() },
      { id: "q2", input: { question: "second" }, createdAt: new Date() },
    ];
    render(<QueueBar queue={queue} />);
    expect(screen.getByTestId("queue-bar")).toBeInTheDocument();
    expect(screen.getByText("Queued 2 messages")).toBeInTheDocument();
    expect(screen.getAllByTestId("queue-entry")).toHaveLength(2);
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText("second")).toBeInTheDocument();
  });

  it("returns null when empty", () => {
    const { container } = render(<QueueBar queue={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("calls onCancel and onClear", async () => {
    const onCancel = vi.fn();
    const onClear = vi.fn();
    const queue: QueuedEntry[] = [
      { id: "q1", input: { question: "a" }, createdAt: new Date() },
      { id: "q2", input: { question: "b" }, createdAt: new Date() },
    ];
    render(<QueueBar queue={queue} onCancel={onCancel} onClear={onClear} />);
    const user = userEvent.setup();
    await user.click(screen.getAllByTestId("queue-cancel")[0]!);
    expect(onCancel).toHaveBeenCalledWith("q1");
    await user.click(screen.getByTestId("queue-clear"));
    expect(onClear).toHaveBeenCalled();
  });
});

function CaptureHarness({ deps, onCapture }: { deps: CoachStreamDeps; onCapture: (c: ReturnType<typeof useCoachStream>) => void }) {
  const chat = useCoachStream(deps);
  useEffect(() => {
    onCapture(chat);
  }, [chat, onCapture]);
  return (
    <>
      {chat.queue.length > 0 && <QueueBar queue={chat.queue} onCancel={chat.cancelQueued} onClear={chat.clearQueue} />}
      <div data-testid="busy-flag">{String(chat.busy)}</div>
      <div data-testid="queue-size">{chat.queue.length}</div>
    </>
  );
}

describe("client submission queue (useCoachStream)", () => {
  it("shares one pending thread creation across immediate sends", async () => {
    const threadId = "55555555-5555-4555-8555-555555555555";
    let releaseCreation: (() => void) | null = null;
    const creationGate = new Promise<void>((resolve) => {
      releaseCreation = resolve;
    });
    const createThread = vi.fn(async () => {
      await creationGate;
      return thread(threadId);
    });
    const stream = fakeStream(() => [updatesPart("coach_agent", [aiMessage("done", "a1")])]);
    const deps = fakeDeps({ createThread }, stream);
    let chat: ReturnType<typeof useCoachStream> | null = null;
    render(<CaptureHarness deps={deps} onCapture={(captured) => { chat = captured; }} />);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

    await act(async () => {
      void chat!.send("first");
      void chat!.send("second");
    });
    await waitFor(() => expect(createThread).toHaveBeenCalledTimes(1));
    expect(stream.calls).toHaveLength(0);

    await act(async () => releaseCreation?.());
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    expect(stream.calls.map((call) => call.options?.threadId)).toEqual([threadId, threadId]);
  });

  it("queues second send while first run pending and drains FIFO without 409", async () => {
    const gate: { release: (() => void) | null } = { release: null };
    let callCount = 0;
    const stream = fakeStream(() =>
      (async function* () {
        callCount += 1;
        if (callCount === 1) {
          await new Promise<void>((resolve) => {
            gate.release = resolve;
          });
        }
        yield updatesPart("coach_agent", [aiMessage(`done-${callCount}`, `a${callCount}`)]);
      })(),
    );
    const deps = fakeDeps({}, stream);
    let chat: ReturnType<typeof useCoachStream> | null = null;
    render(<CaptureHarness deps={deps} onCapture={(c) => { chat = c; }} />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(chat).not.toBeNull();

    await act(async () => {
      void chat!.send("first");
    });
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.payload).toEqual({ input: { question: "first" } });
    expect(stream.calls[0]?.options).toMatchObject({
      streamMode: ["updates"],
      multitaskStrategy: "reject",
    });

    await act(async () => {
      void chat!.send("second");
    });
    await waitFor(() => expect(screen.getByTestId("queue-bar")).toBeInTheDocument());
    expect(stream.calls).toHaveLength(1);
    expect(screen.getByText("Queued 1 message")).toBeInTheDocument();
    expect(screen.getByText("second")).toBeInTheDocument();
    expect(screen.queryByText(/409|already in flight/i)).toBeNull();

    await act(async () => {
      gate.release?.();
    });

    await waitFor(() => expect(stream.calls).toHaveLength(2), { timeout: 2000 });
    expect(stream.calls[1]?.payload).toEqual({ input: { question: "second" } });

    await waitFor(() => expect(screen.queryByTestId("queue-bar")).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("server enqueue path: v2 uses multitaskStrategy enqueue", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    const stream = fakeStream(() => [updatesPart("coach_agent", [aiMessage("Queued.", "a1")])]);
    const deps = fakeDeps({}, stream);
    render(<ChatShell deps={deps} email="member@example.com" onSignedOut={() => {}} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message your coach"), "hello v2");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.options).toMatchObject({
      streamMode: ["updates", "messages"],
      streamResumable: true,
      multitaskStrategy: "enqueue",
    });
    vi.unstubAllEnvs();
  });

  it("queue drains in order for three rapid sends", async () => {
    const gates: Array<() => void> = [];
    let callCount = 0;
    const stream = fakeStream(() =>
      (async function* () {
        callCount += 1;
        const idx = callCount;
        if (idx <= 3) {
          await new Promise<void>((resolve) => {
            gates[idx - 1] = resolve;
          });
        }
        yield updatesPart("coach_agent", [aiMessage(`done-${idx}`, `a${idx}`)]);
      })(),
    );
    const deps = fakeDeps({}, stream);
    let chat: ReturnType<typeof useCoachStream> | null = null;
    render(<CaptureHarness deps={deps} onCapture={(c) => { chat = c; }} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    await act(async () => { void chat!.send("one"); });
    await waitFor(() => expect(stream.calls).toHaveLength(1));

    await act(async () => { void chat!.send("two"); });
    await act(async () => { void chat!.send("three"); });

    expect(stream.calls).toHaveLength(1);
    expect(await screen.findByText("Queued 2 messages")).toBeInTheDocument();

    await act(async () => gates[0]?.());
    await waitFor(() => expect(stream.calls).toHaveLength(2));
    expect(stream.calls[1]?.payload).toEqual({ input: { question: "two" } });

    await act(async () => gates[1]?.());
    await waitFor(() => expect(stream.calls).toHaveLength(3));
    expect(stream.calls[2]?.payload).toEqual({ input: { question: "three" } });

    await act(async () => gates[2]?.());
    await waitFor(() => expect(screen.queryByTestId("queue-bar")).toBeNull());
  });
});
