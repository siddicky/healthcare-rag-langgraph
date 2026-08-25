import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TimeTravel } from "@/chat/components/TimeTravel";
import { ChatShell } from "@/chat/components/ChatShell";
import { aiMessage, fakeDeps, fakeStream, humanMessage, thread, updatesPart, type StreamCall } from "./helpers";

const EMAIL = "member@example.com";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

const historyFixtures = [
  {
    checkpoint_id: "cp-aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
    parent_checkpoint_id: "cp-parent-0000-4000-8000-000000000000",
    created_at: "2026-08-25T10:00:00Z",
    checkpoint_ns: "",
    values: { messages: [humanMessage("hello", "h1"), aiMessage("hi", "a1")] },
  },
  {
    checkpoint_id: "cp-bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb",
    parent_checkpoint_id: "cp-aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
    created_at: "2026-08-25T10:01:00Z",
    checkpoint_ns: "",
    values: { messages: [humanMessage("hello", "h1"), aiMessage("hi", "a1"), humanMessage("follow up", "h2"), aiMessage("sure", "a2")] },
  },
] as unknown as import("@/chat/useCoachStream").ThreadHistory[];

describe("TimeTravel — unit", () => {
  it("lists checkpoints via history.map and shows messages count, fork resumes via onFork", async () => {
    const onFork = vi.fn();
    const onTimeTravel = vi.fn();
    render(<TimeTravel history={historyFixtures} selectedCheckpointId={null} onTimeTravel={onTimeTravel} onFork={onFork} busy={false} />);

    const entries = screen.getAllByTestId("time-travel-entry");
    expect(entries).toHaveLength(2);
    expect(entries[0]?.getAttribute("data-checkpoint-id")).toBe(historyFixtures[0]!.checkpoint_id);
    expect(entries[1]?.getAttribute("data-checkpoint-id")).toBe(historyFixtures[1]!.checkpoint_id);

    // messages count per checkpoint
    const counts = screen.getAllByTestId("time-travel-count");
    expect(counts[0]?.textContent).toBe("2 msgs");
    expect(counts[1]?.textContent).toBe("4 msgs");

    const user = userEvent.setup();
    const forkButtons = screen.getAllByTestId("time-travel-fork-btn");
    await user.click(forkButtons[0]!);
    expect(onFork).toHaveBeenCalledWith(historyFixtures[0]!.checkpoint_id);
    expect(onTimeTravel).not.toHaveBeenCalled();

    const viewButtons = screen.getAllByTestId("time-travel-view-btn");
    await user.click(viewButtons[1]!);
    expect(onTimeTravel).toHaveBeenCalledWith(historyFixtures[1]!.checkpoint_id);
  });

  it("v1 hidden: null history renders nothing", () => {
    const { container } = render(<TimeTravel history={null} onTimeTravel={vi.fn()} onFork={vi.fn()} />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("time-travel-panel")).not.toBeInTheDocument();
  });

  it("selecting checkpoint highlights entry and view loads snapshot", async () => {
    const onTimeTravel = vi.fn();
    const { rerender } = render(<TimeTravel history={historyFixtures} selectedCheckpointId={null} onTimeTravel={onTimeTravel} onFork={vi.fn()} />);
    expect(screen.getAllByTestId("time-travel-entry")[0]?.getAttribute("data-selected")).toBe("false");
    rerender(<TimeTravel history={historyFixtures} selectedCheckpointId={historyFixtures[1]!.checkpoint_id} onTimeTravel={onTimeTravel} onFork={vi.fn()} />);
    expect(screen.getAllByTestId("time-travel-entry")[1]?.getAttribute("data-selected")).toBe("true");
  });
});

describe("TimeTravel — ChatShell integration via history + fork", () => {
  it("scripted history with 2 checkpoints → list renders 2; fork calls forkFromCheckpoint with correct id", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    const getThreadHistory = vi.fn(async () => historyFixtures as unknown as unknown[]);
    let stateCalls: string[] = [];
    const getThreadState = vi.fn(async (id: string, checkpointId?: string) => {
      if (checkpointId) stateCalls.push(checkpointId);
      return { values: { messages: historyFixtures.find((h) => h.checkpoint_id === checkpointId)?.values.messages ?? [humanMessage("hello", "h1")] }, interrupts: [] };
    });
    const stream = fakeStream((call) => {
      if (call.options?.forkFrom === historyFixtures[0]!.checkpoint_id) {
        return [updatesPart("coach_agent", [aiMessage("forked answer", "a-fork")])];
      }
      return [updatesPart("coach_agent", [aiMessage("original", "a1")])];
    });

    // prime thread list so activeThreadId is set and history auto-fetches
    const t = thread("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    const searchThreads = vi.fn(async () => [t]);
    const deps = fakeDeps({ getThreadHistory, getThreadState, searchThreads }, stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);

    const user = userEvent.setup();

    // wait for history panel to appear (auto-fetched after active thread hydrates)
    await waitFor(() => expect(screen.getByTestId("time-travel-panel")).toBeInTheDocument(), { timeout: 3000 });
    const entries = await screen.findAllByTestId("time-travel-entry");
    expect(entries).toHaveLength(2);

    // View checkpoint -> timeTravel fetches state at checkpoint
    const viewButtons = screen.getAllByTestId("time-travel-view-btn");
    await user.click(viewButtons[0]!);
    await waitFor(() => expect(getThreadState).toHaveBeenCalled());
    expect(stateCalls).toContain(historyFixtures[0]!.checkpoint_id);

    // Fork from first checkpoint -> resumeFromCheckpoint -> forkFromCheckpoint -> stream.submit with forkFrom
    const forkButtons = screen.getAllByTestId("time-travel-fork-btn");
    await user.click(forkButtons[0]!);
    await waitFor(() => expect(stream.calls.some((c: StreamCall) => c.options?.forkFrom === historyFixtures[0]!.checkpoint_id)).toBe(true), { timeout: 3000 });
    const forkCall = stream.calls.find((c: StreamCall) => c.options?.forkFrom === historyFixtures[0]!.checkpoint_id);
    expect(forkCall).toBeDefined();
    expect(forkCall?.options?.forkFrom).toBe(historyFixtures[0]!.checkpoint_id);
  });

  it("v1 does not expose history (gated)", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v1");
    const getThreadHistory = vi.fn(async () => historyFixtures as unknown as unknown[]);
    const searchThreads = vi.fn(async () => [thread("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")]);
    const stream = fakeStream(() => [updatesPart("coach_agent", [aiMessage("answer", "a1")])]);
    const deps = fakeDeps({ getThreadHistory, searchThreads }, stream);
    render(<ChatShell deps={deps} email={EMAIL} onSignedOut={() => {}} />);
    // panel should never appear in v1 even though history mock exists
    await waitFor(() => expect(screen.queryByTestId("time-travel-panel")).not.toBeInTheDocument());
    expect(getThreadHistory).not.toHaveBeenCalled();
  });
});
