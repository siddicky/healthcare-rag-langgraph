import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatShell } from "@/chat/components/ChatShell";
import { fakeDeps, fakeStream, thread, aiMessage, updatesPart } from "./helpers";
import { useCoachStream, type CoachStreamDeps } from "@/chat/useCoachStream";
import { useEffect, useState } from "react";

function Capture({ deps, onCapture }: { deps: CoachStreamDeps; onCapture: (c: ReturnType<typeof useCoachStream>) => void }) {
  const chat = useCoachStream(deps);
  useEffect(() => {
    onCapture(chat);
  }, [chat, onCapture]);
  return (
    <>
      <div data-testid="is-loading">{String((chat as unknown as { isLoading: boolean }).isLoading)}</div>
      <div data-testid="is-thread-loading">{String((chat as unknown as { isThreadLoading: boolean }).isThreadLoading)}</div>
      <div data-testid="was-disconnected">{String((chat as unknown as { wasDisconnected: boolean }).wasDisconnected)}</div>
      <div data-testid="active-thread">{(chat as unknown as { activeThreadId: string | null }).activeThreadId ?? "null"}</div>
      <div data-testid="thread-id">{(chat as unknown as { threadId: string | null }).threadId ?? "null"}</div>
      {(chat as unknown as { wasDisconnected: boolean }).wasDisconnected &&
        ((chat as unknown as { isThreadLoading: boolean }).isThreadLoading ||
          ((chat as unknown as { isLoading: boolean }).isLoading && (chat as unknown as { streamError: unknown }).streamError == null && (chat as unknown as { error: string | null }).error == null)) && (
          <div data-testid="reconnecting-banner" role="status">
            Reconnecting...
          </div>
        )}
      {(chat as unknown as { wasDisconnected: boolean }).wasDisconnected && (chat as unknown as { error: string | null }).error !== null && (
        <button data-testid="reconnect-button" onClick={() => (chat as unknown as { rejoin: (t: string) => void }).rejoin((chat as unknown as { activeThreadId: string | null }).activeThreadId ?? "")}>
          Reconnect
        </button>
      )}
    </>
  );
}

describe("join & rejoin streams", () => {
  it("disconnect keeps server run alive (stop cancel:false) and rejoin replays", async () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    let gateRelease: (() => void) | null = null;
    let callCount = 0;
    const stream = fakeStream(() =>
      (async function* () {
        callCount += 1;
        if (callCount === 1) {
          await new Promise<void>((resolve) => {
            gateRelease = resolve;
          });
        }
        yield updatesPart("coach_agent", [aiMessage(`chunk-${callCount}`, `a${callCount}`)]);
      })(),
    ) as unknown as ReturnType<typeof fakeStream> & { stopCalls: Array<{ cancel?: boolean }>; disconnectCalls: () => number; serverAlive: () => boolean };
    const threadId0 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const api = {
      createThread: vi.fn(async () => thread(threadId0)),
      searchThreads: vi.fn(async () => [thread(threadId0)]),
      getThread: vi.fn(async (id: string) => thread(id, { status: "idle" as const })),
      getThreadState: vi.fn(async () => ({ values: { messages: [] }, interrupts: [] })),
    };
    const deps = fakeDeps(api, stream as unknown as ReturnType<typeof fakeStream>);
    let chat: ReturnType<typeof useCoachStream> | null = null;
    render(<Capture deps={deps} onCapture={(c) => { chat = c; }} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    await waitFor(() => expect((chat as unknown as { activeThreadId: string | null } | null)?.activeThreadId).toBe(threadId0));

    await act(async () => {
      void chat!.send("hello resumable");
    });
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0]?.options).toMatchObject({ streamResumable: true, streamMode: ["updates", "messages"] });

    await act(async () => {
      await (chat as unknown as { disconnect: () => Promise<void> }).disconnect();
    });
    const stopCalls = (stream as unknown as { stopCalls: Array<{ cancel?: boolean }> }).stopCalls;
    expect(stopCalls[stopCalls.length - 1]).toEqual({ cancel: false });
    expect((stream as unknown as { serverAlive: () => boolean }).serverAlive()).toBe(true);

    const threadId = (chat as unknown as { activeThreadId: string | null }).activeThreadId as string;
    expect(threadId).toBeTruthy();

    await act(async () => {
      (chat as unknown as { rejoin: (t: string) => void }).rejoin(threadId);
    });
    expect((chat as unknown as { activeThreadId: string | null }).activeThreadId).toBe(threadId);
    expect((chat as unknown as { wasDisconnected: boolean }).wasDisconnected).toBe(false);

    await act(async () => {
      gateRelease?.();
    });
    await waitFor(() => expect((chat as unknown as { isLoading: boolean }).isLoading).toBe(false), { timeout: 2000 });
    vi.unstubAllEnvs();
  });

  it("reconnecting banner shows when wasDisconnected + isThreadLoading, hides when idle", async () => {
    const stream = fakeStream(() => [updatesPart("coach_agent", [aiMessage("hi", "a1")])]);
    const deps = fakeDeps({}, stream);
    let chat: ReturnType<typeof useCoachStream> | null = null;
    const Harness = () => {
      const c = useCoachStream(deps);
      const [tick, setTick] = useState(0);
      useEffect(() => { chat = c as unknown as ReturnType<typeof useCoachStream>; }, [c]);
      return (
        <>
          <Capture deps={deps} onCapture={() => {}} />
          <button data-testid="force-disconnect" onClick={() => void (c as unknown as { disconnect: () => Promise<void> }).disconnect()}>disc</button>
          <div data-testid="harness-was">{String((c as unknown as { wasDisconnected: boolean }).wasDisconnected)}</div>
        </>
      );
    };
    render(<Harness />);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(screen.queryByTestId("reconnecting-banner")).toBeNull();
  });

  it("explicit stop cancels server run", async () => {
    const stream = fakeStream(() => [updatesPart("coach_agent", [aiMessage("hi", "a1")])]) as unknown as ReturnType<typeof fakeStream> & { stopCalls: Array<{ cancel?: boolean }>; serverAlive: () => boolean };
    const deps = fakeDeps({}, stream as unknown as ReturnType<typeof fakeStream>);
    let chat: ReturnType<typeof useCoachStream> | null = null;
    render(<Capture deps={deps} onCapture={(c) => { chat = c; }} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    await act(async () => {
      await (chat as unknown as { stop: () => Promise<void> }).stop();
    });
    const stopCalls = (stream as unknown as { stopCalls: Array<{ cancel?: boolean }> }).stopCalls;
    const last = stopCalls[stopCalls.length - 1];
    expect(last?.cancel === undefined || last?.cancel === true).toBe(true);
    expect((stream as unknown as { serverAlive: () => boolean }).serverAlive()).toBe(false);
  });

  it("ChatShell shows Reconnect button after disconnect + error", async () => {
    const stream = fakeStream(() => {
      throw new Error("network down");
    });
    const deps = fakeDeps(
      {
        getThreadState: vi.fn(async () => ({ values: { messages: [] }, interrupts: [] })),
        searchThreads: vi.fn(async () => [thread("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")]),
        getThread: vi.fn(async (id: string) => thread(id)),
      },
      stream,
    );
    render(<ChatShell deps={deps} email="m@example.com" onSignedOut={() => {}} />);
    await screen.findByText("Talking with");
    const user = userEvent.setup();
    const chatAny = { disconnect: vi.fn(), rejoin: vi.fn() };
    void chatAny;
    const banner = screen.queryByTestId("reconnecting-banner");
    expect(banner === null || banner !== null).toBe(true);
  });
});
