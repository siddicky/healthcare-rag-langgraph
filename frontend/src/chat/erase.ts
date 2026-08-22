import type { ThreadStatus } from "./coachProtocol";

/**
 * Erasure phase 2 (client-driven, v19 contract): the server's
 * `erase_my_data` node has already wiped the store and emitted the
 * `erase_confirmation_v1` marker. The client then:
 *
 *   1. waits for run terminality (a clean stream EOF is terminal; after a
 *      disconnect, poll GET /threads/{id} until status != "busy"),
 *   2. snapshots ALL owned threads (threads/search, thread_id asc, fully
 *      paginated — never delete during pagination),
 *   3. deletes every non-current thread in ascending order, the marker-
 *      bearing current thread LAST,
 *   4. FAIL-STOP: a non-current DELETE failure halts the flow and preserves
 *      the marker thread so the member can retry (resume re-paginates and
 *      detects the persisted marker).
 */

export interface EraseFlowDeps {
  getThreadStatus(threadId: string): Promise<ThreadStatus>;
  searchPage(limit: number, offset: number): Promise<string[]>;
  deleteThread(threadId: string): Promise<void>;
  sleep(ms: number): Promise<void>;
}

export type ErasePhase = "waiting" | "deleting" | "done" | "failed";

export interface EraseOutcome {
  phase: ErasePhase;
  /** Human-readable failure note (FAIL-STOP reason). */
  note: string | null;
  /** Thread ids the flow did NOT delete (retry candidates). */
  remaining: string[];
}

export const ERASE_SEARCH_PAGE_SIZE = 100;

/** Fully paginate threads/search (thread_id asc) into one snapshot. */
export async function snapshotOwnedThreadIds(deps: EraseFlowDeps): Promise<string[]> {
  const ids: string[] = [];
  let offset = 0;
  for (;;) {
    const page = await deps.searchPage(ERASE_SEARCH_PAGE_SIZE, offset);
    ids.push(...page);
    if (page.length < ERASE_SEARCH_PAGE_SIZE) break;
    offset += page.length;
  }
  return ids;
}

export interface EraseWaitOptions {
  pollMs: number;
  maxPolls: number;
}

/** Shared terminality rule: poll until status != busy (bounded). */
export async function waitUntilNotBusy(
  deps: EraseFlowDeps,
  threadId: string,
  options: EraseWaitOptions,
): Promise<boolean> {
  for (let attempt = 0; attempt < options.maxPolls; attempt += 1) {
    if (attempt > 0) await deps.sleep(options.pollMs);
    const status = await deps.getThreadStatus(threadId);
    if (status !== "busy") return true;
  }
  return false;
}

/**
 * Run phase 2 against the marker-bearing thread. Returns `waiting` (zero
 * DELETEs issued) while the thread is still busy; `failed` preserves the
 * current thread for retry.
 */
export async function runErasePhase2(
  deps: EraseFlowDeps,
  currentThreadId: string,
  options: EraseWaitOptions,
): Promise<EraseOutcome> {
  const terminal = await waitUntilNotBusy(deps, currentThreadId, options);
  if (!terminal) {
    return { phase: "waiting", note: "The erase request is still running.", remaining: [] };
  }
  const snapshot = await snapshotOwnedThreadIds(deps);
  const others = snapshot
    .filter((id) => id !== currentThreadId)
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  for (const id of others) {
    try {
      await deps.deleteThread(id);
    } catch (error) {
      const note = error instanceof Error ? error.message : "Thread deletion failed.";
      return {
        phase: "failed",
        note,
        remaining: [id, ...others.slice(others.indexOf(id) + 1), currentThreadId],
      };
    }
  }
  try {
    await deps.deleteThread(currentThreadId);
  } catch (error) {
    const note = error instanceof Error ? error.message : "Thread deletion failed.";
    return { phase: "failed", note, remaining: [currentThreadId] };
  }
  return { phase: "done", note: null, remaining: [] };
}
