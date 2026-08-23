<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# e2e

## Purpose
Hermetic Playwright end-to-end tests for the coach frontend. `server.py` is a Python orchestrator that boots the FULL local stack with ZERO external network: a scripted dependency server (fake OpenAI gateway + Supabase stub + LangSmith feedback mirror), a real `langgraph dev` Agent Server running the actual coach graph with two offline seams (deterministic safety classifier, offline Route-A monograph answer graph), and a production `next start` frontend built against that server. `smoke.spec.ts` drives two member identities (`u1`, `u2`) through the full product surface — chat, generative-ui cards, interrupts, reminders, document upload, regenerate, feedback, branching — against this stack. `run.ts` holds shared helpers (runfile reading, envelope extraction, date math). `global-setup.ts`/`global-teardown.ts` start/stop `server.py` around the whole Playwright run.

## Key Files
| File | Description |
|------|-------------|
| `server.py` | The hermetic orchestrator: scripted OpenAI-shaped gateway, Supabase stub, LangSmith feedback mirror, spawns `langgraph dev` with offline seams (`gate.GATEWAY`, `rag_relay.child`), spawns `next start`; writes readiness + connection info to a JSON runfile; tears everything down on SIGTERM/SIGINT/exit |
| `run.ts` | `readRun()` (reads `COACH_E2E_RUNFILE`, default `.tmp/run.json`), `memberApi()`/`internalHeaders()` (authenticated `APIRequestContext` helpers), `threadState()`/`envelopesOf()` (pulls DATA envelopes out of a thread's message channel — mirrors `@/catalog/envelopes.parseDataEnvelope` in TS for test assertions), date helpers (`nextWeekday`, `nextFriday`, `monthOf`) mirroring `server.py`'s own date logic so expectations are derived, not hardcoded |
| `smoke.spec.ts` | The full suite, `test.describe.configure({mode: "serial"})` (tests share `shared` thread-id state and run in a fixed order): u1's full journey (route-A answer, TrendCard hydration with delta, calendar interrupt confirm/decline/double-click-guard, MiniCalendar month views, reminders, document upload/extraction, regenerate, feedback, branch), u2 cross-identity isolation, wrong-password login, and a perimeter-sentinel rejected-route check |
| `global-setup.ts` | Spawns `server.py`, polls the runfile for `ready: true` (600s boot timeout), sets `COACH_E2E_RUNFILE`/`COACH_E2E_BASE_URL`, writes the child PID to `.tmp/server.pid` |
| `global-teardown.ts` | Reads `.tmp/server.pid`, sends `SIGTERM`, polls up to 30s, then `SIGKILL`s if still alive |
| `fixtures/intake.pdf` | The sample document used to exercise the upload -> scan -> extract -> `MemoryExtractionCard` review flow |
| `.tmp/` | Generated at test-run time: `run.json` (the runfile), `server.pid`, `server-stdout.log`, `test-results/` — gitignored, safe to delete between runs |
| `__screenshots__/` | Playwright screenshots taken mid-test (e.g. `trend-card.png`, `calendar-change-confirmed.png`) for visual review, not pixel-diffed in CI |

## For AI Agents

### Working In This Directory
- The suite is **serial and stateful by design** (`fullyParallel: false`, `workers: 1` in `../playwright.config.ts`, `test.describe.configure({mode: "serial"})`): tests share `shared.u1ThreadId`/`u2ThreadId` and assume prior steps' side effects. Don't parallelize or reorder tests without re-threading that shared state.
- There is no static `baseURL` — ports are allocated by `server.py` at boot. Always navigate with `run.frontend_url` (from `readRun()`), never a hardcoded `localhost:3000`.
- `envelopesOf()` in `run.ts` and `parseDataEnvelope()` in `@/catalog/envelopes.ts` implement the SAME envelope-detection logic independently (one in TS test code, one in the app) — if the envelope wire shape changes, update both, and prefer asserting against real envelope data (`latestEnvelope()`) over hardcoded expected strings so tests don't silently drift from the real contract.
- `server.py`'s offline seams (`gate.GATEWAY`, `rag_relay.child`) are injected at import time into a generated graph module — this is the same pattern as `scripts/coach_smoke.py`/`scripts/deployed_smoke.py` at the repo root; keep this suite's seams consistent with those if the coach graph's public interface changes.
- Screenshots in `__screenshots__/` are for human visual review, not asserted against in the test bodies — don't build pass/fail logic on their contents.

### Testing Requirements
- Run via `bun --cwd frontend run playwright`. Expect a long first run (`BOOT_TIMEOUT_MS = 600_000` — up to 10 minutes to boot `langgraph dev` + `next start` + the scripted gateway) and an overall per-test timeout of 240s (the u1 journey test extends itself to 300s via `test.setTimeout`).
- A failed boot writes buffered stdout/stderr to `.tmp/server-stdout.log` and surfaces it in the thrown error — check that log first when `global-setup.ts` times out.
- The parity suites at the repo root (`scripts/langgraph_smoke.py`, `scripts/deployed_smoke.py`) are the authoritative server contract per `../../AGENTS.md`; a divergence between this frontend suite and those means the SERVER is wrong, never this suite — but keep this suite's assumptions about run/resume/interrupt shapes aligned with theirs.

### Common Patterns
- Helper functions in `smoke.spec.ts` (`login`, `waitForIdle`, `send`, `expectAssistantCount`, `confirmInterrupt`, `latestEnvelope`, `reminderPresent`, `trackThreadCreations`) are the vocabulary the whole suite is written in — extend this vocabulary for new flows rather than inlining raw `page.locator`/`page.fill` calls in new tests.
- Date-dependent assertions always derive expected values from the same date-math helpers `server.py` uses (`nextWeekday`, `nextFriday`, `monthOf` in `run.ts`), never hardcoded calendar dates, so the suite doesn't rot as the run date changes.

## Dependencies

### Internal
- The coach graph (`healthcare_rag/agent/`), the perimeter (`healthcare_rag/agent/perimeter.py`), and `langgraph dev` — all driven by `server.py`
- `@/catalog/envelopes` — mirrored (not imported) by `run.ts`'s `envelopesOf()`

### External
- `@playwright/test`, Python 3.12 + `httpx` (via the repo's `.venv`, spawned by `global-setup.ts`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
