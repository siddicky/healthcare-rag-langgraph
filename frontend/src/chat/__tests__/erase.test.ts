import { describe, expect, it, vi } from "vitest";
import {
  ERASE_SEARCH_PAGE_SIZE,
  runErasePhase2,
  snapshotOwnedThreadIds,
  type EraseFlowDeps,
} from "@/chat/erase";

function makeDeps(overrides: Partial<EraseFlowDeps> = {}): EraseFlowDeps & {
  deletes: string[];
  statusCalls: string[];
} {
  const deletes: string[] = [];
  const statusCalls: string[] = [];
  return {
    deletes,
    statusCalls,
    getThreadStatus: async (id: string) => {
      statusCalls.push(id);
      void id;
      return "idle";
    },
    searchPage: vi.fn(async () => [] as string[]),
    deleteThread: async (id: string) => {
      deletes.push(id);
    },
    sleep: vi.fn(async () => undefined),
    ...overrides,
  };
}

const OPTS = { pollMs: 0, maxPolls: 5 };

describe("runErasePhase2 (v19 fixtures)", () => {
  it("busy thread → WAIT and zero DELETEs (terminality precedes any deletion)", async () => {
    const statusCalls: string[] = [];
    const deps = makeDeps({
      getThreadStatus: async (id: string) => {
        statusCalls.push(id);
        return "busy";
      },
    });
    const outcome = await runErasePhase2(deps, "current", OPTS);
    expect(outcome.phase).toBe("waiting");
    expect(deps.deletes).toEqual([]);
    expect(statusCalls).toHaveLength(OPTS.maxPolls);
  });

  it("idle + marker → snapshot-then-delete: thread_id asc, current LAST, cleanup done", async () => {
    const deps = makeDeps({
      searchPage: async (limit, offset) => {
        expect(limit).toBe(ERASE_SEARCH_PAGE_SIZE);
        if (offset === 0) return ["cccc", "aaaa", "current", "bbbb"];
        return [];
      },
    });
    const outcome = await runErasePhase2(deps, "current", OPTS);
    expect(outcome.phase).toBe("done");
    expect(deps.deletes).toEqual(["aaaa", "bbbb", "cccc", "current"]);
  });

  it("FAIL-STOP: a non-current DELETE failure halts and preserves the marker thread", async () => {
    const deps = makeDeps({
      searchPage: async () => ["zzzz", "ffff", "current"],
      deleteThread: async (id: string) => {
        deps.deletes.push(id);
        if (id === "ffff") throw new Error("deletion blocked");
      },
    });
    const outcome = await runErasePhase2(deps, "current", OPTS);
    expect(outcome.phase).toBe("failed");
    expect(deps.deletes).toEqual(["ffff"]);
    expect(outcome.remaining).toContain("current");
    expect(outcome.note).toBe("deletion blocked");
  });

  it("disconnect resume: re-paginates, skips already-deleted threads, finishes with current", async () => {
    let firstRun = true;
    const existing = new Set(["aaaa", "current"]);
    const deps = makeDeps({
      searchPage: vi.fn(async () => (firstRun ? ["aaaa", "bbbb", "current"] : [...existing])),
      deleteThread: async (id: string) => {
        deps.deletes.push(id);
        existing.delete(id);
      },
    });
    const interrupted = await runErasePhase2(
      {
        ...deps,
        deleteThread: async (id) => {
          deps.deletes.push(id);
          if (id === "bbbb") throw new Error("network dropped");
          existing.delete(id);
        },
      },
      "current",
      OPTS,
    );
    expect(interrupted.phase).toBe("failed");
    expect(existing.has("current")).toBe(true);

    firstRun = false;
    const resumed = await runErasePhase2(deps, "current", OPTS);
    expect(resumed.phase).toBe("done");
    expect(existing).toEqual(new Set());
  });

  it("paginates the full snapshot before deleting (never deletes during pagination)", async () => {
    const pages = new Map<number, string[]>([
      [0, Array.from({ length: ERASE_SEARCH_PAGE_SIZE }, (_, i) => `t${String(i).padStart(3, "0")}`)],
      [ERASE_SEARCH_PAGE_SIZE, ["current"]],
    ]);
    const deps = makeDeps({
      searchPage: vi.fn(async (_limit, offset) => pages.get(offset) ?? []),
    });
    const ids = await snapshotOwnedThreadIds(deps);
    expect(ids).toHaveLength(ERASE_SEARCH_PAGE_SIZE + 1);
    expect(deps.deletes).toEqual([]);
    const outcome = await runErasePhase2(deps, "current", OPTS);
    expect(outcome.phase).toBe("done");
    expect(deps.deletes[deps.deletes.length - 1]).toBe("current");
    expect(deps.deletes).toHaveLength(ERASE_SEARCH_PAGE_SIZE + 1);
  });
});
