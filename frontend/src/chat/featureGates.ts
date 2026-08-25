/**
 * Feature gates for optional coach-chat UI surfaces.
 *
 * Owner directive: the history (TimeTravel) + branching UI is "not required
 * for now", so it ships DISABLED unless the build/runtime env explicitly
 * enables it with the exact string "1". The gate is read at call time (same
 * pattern as `isForkV2Enabled()` in useCoachStream.ts) so tests can flip it
 * with `vi.stubEnv` and builds can bake it via NEXT_PUBLIC_* inlining.
 *
 * When off, only the UI affordances are hidden — the underlying hook
 * functions (`branch`, `fetchHistory`, `timeTravel`, …) stay callable from
 * code so nothing is deleted.
 */
export function isHistoryBranchUiEnabled(): boolean {
  return process.env.NEXT_PUBLIC_COACH_HISTORY_BRANCH_UI === "1";
}
