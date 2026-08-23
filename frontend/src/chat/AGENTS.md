<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/chat

## Purpose
The coach chat domain layer: the member-perimeter wire protocol (`coachProtocol.ts`), the HTTP/SDK client (`coachApi.ts`, wired to production deps in `coachClient.ts`), the LangChain-serialized message model and turn-splitting logic (`model.ts`), the updates-only stream reducer (`stream.ts`), the client-driven erase flow (`erase.ts`), the document upload state machine (`uploadFlow.ts`), local thread titles (`titles.ts`), and the `useCoachChat` controller hook that ties all of it into the UI. `components/` holds the presentational React for the chat screen (composer, message list, sidebar, action bar, interrupt panel). Everything network-facing is injected as a `CoachChatDeps` bundle so tests can drive the whole controller from scripted fakes with zero real network or timers.

## Key Files
| File | Description |
|------|-------------|
| `coachApi.ts` | Every member-facing HTTP call (`createThread`, `searchThreads`, `getThread`, `deleteThread`, `copyThread`, `getThreadState`, `postUpload`, `getUploadStatus`, `postFeedback`, `streamRun`), shaped byte-for-byte to what `healthcare_rag/agent/perimeter.py` allows; `CoachApiError` carries the HTTP status |
| `coachClient.ts` | `createBrowserDeps()` — production wiring: a `CoachFetch` bound to the Supabase-refreshed bearer, the SDK stream client, real `crypto.randomUUID()`/`setTimeout`, and the poll intervals (erase: 1500ms/40; upload: 1200ms/50). Memoized singleton; tests never touch this |
| `coachProtocol.ts` | Wire protocol constants: `SENTINEL_QUESTION`, `ERASE_MARKER_NAME`/`ERASE_MARKER_CONTENT`, `RENDERED_NODE_NAMES` (8 allow-listed graph nodes), `THREAD_SELECT_FIELDS`, `RUN_ASSISTANT_ID`/`RUN_STREAM_PARAMS` (fixed run envelope), `RunInput`/`ResumePayload` types, `MUTATING_TOOL_PREFIXES`, `UploadStage`, `OPENERS`/`UPLOAD_OPENER` |
| `model.ts` | `WireMessage`/`TurnModel` types, `buildTurns()` (splits a flat message list into HumanMessage-bounded turns, extracting DATA envelopes per turn), `mergeMessages()` (id-deduped insertion-ordered merge), `regenerateEligibility()` (latest-turn-only gate), interrupt payload schemas/classifiers (`classifyInterruptPayload`), reminder-delivery and memory-confirmation parsers, `aiDisplayText()` |
| `stream.ts` | `applyStreamPart()` — pure reducer folding one SDK stream event into `{messages, interruptValue}`; only `RENDERED_NODE_NAMES` nodes are read, human echoes from the stream are dropped; `consumeRunStream()` drives an `AsyncIterable` to completion against a caller-owned accumulator |
| `erase.ts` | Phase-2 (client-driven) erasure: `waitUntilNotBusy()`, `snapshotOwnedThreadIds()` (fully paginated, thread_id-asc), `runErasePhase2()` — deletes non-current threads first, marker thread LAST, fail-stop on any DELETE failure |
| `uploadFlow.ts` | `UploadUi` state machine (`idle`/`inflight`/`staged`/`failed`), `applyUploadEvent()` reducer, `shouldPollStatus()`, `documentStage()`, `formatFileSize()` — stage progression comes ONLY from server responses, never client timers |
| `titles.ts` | Client-local thread titles in `localStorage` (`nymble:thread-titles`) — the server never stores a display title |
| `useCoachChat.ts` | The controller hook: owns all chat state (threads, messages, pendingInterrupt, busy, upload, erase, feedback), exposes `send`/`attach`/`approveInterrupt`/`regenerate`/`branch`/`sendFeedback`/`newConversation`/`selectThread`/`removeThread`/`signOut`; takes a `CoachChatDeps` bundle for full dependency injection |
| `components/` | Presentational React for the chat screen (see `components/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Every network/timer/random seam in `useCoachChat` goes through the injected `deps: CoachChatDeps` — never call `fetch`, `setTimeout`, or `crypto.randomUUID()` directly inside this hook or its helpers; add the seam to `CoachChatDeps` instead.
- `messagesRef`/`activeThreadRef`/`uploadRef`/`pendingInterruptRef` mirror their state counterparts so callbacks (which close over stale state in React) can read the latest value synchronously — keep new pieces of mutable-during-async state in a ref the same way.
- `RENDERED_NODE_NAMES` in `coachProtocol.ts` is the allow-list for which graph node updates the stream reducer reads; a new graph node must be added there (and the reducer/test) before its messages will render — anything else is dropped with `chatTelemetry({kind: "unknown_node"})`.
- Regenerate/branch/feedback eligibility is turn-local: `regenerateEligibility()` looks ONLY at the latest turn's HumanMessage-bounded window (no tool calls, no ToolMessages, no interrupt, no erase marker, not the sentinel question) — never make it consider an older turn.
- One active run per thread: `runStream()` early-returns if `busyRef.current` is true; don't bypass `busyRef` when adding new mutating actions.
- The erase flow is fail-stop by design: `runErasePhase2()` must preserve the marker-bearing current thread on any failure so a retry can resume from a persisted marker — don't change it to best-effort/continue-on-error.

### Testing Requirements
- `bun --cwd frontend run test` covers `src/chat/__tests__/`: `chat.test.tsx` (full `useCoachChat` + `ChatShell` integration against scripted `CoachChatDeps`), `model.test.ts`, `protocol.test.ts`, `streamWire.test.ts`, `erase.test.ts`, `documentStages.test.ts`, `actionBar.test.tsx`, `genUi.test.tsx` (catalog integration from the chat side), `forbiddenModes.test.ts` (protocol envelope shape assertions), `helpers.ts` (shared scripted-deps builders).
- New chat behavior should be testable by scripting `CoachChatDeps` (see `helpers.ts`) rather than mocking `fetch`/timers directly.

### Common Patterns
- Pure-reducer-plus-thin-hook: state transition logic (`applyStreamPart`, `applyUploadEvent`, `runErasePhase2`, `regenerateEligibility`) is written as pure functions taking explicit inputs and returning new state/outcome, independently testable; `useCoachChat` is the only place that wires them to React state and the injected deps.
- Card JSON never renders as raw text: any AI message content starting with `{` is checked via `parseComponentCard`/`parseReminderDelivery`/`parseMemoryConfirmation` before falling back to plain `messageText()`.

## Dependencies

### Internal
- `@/catalog/envelopes` (`parseDataEnvelope`, `DataEnvelope`) — turn model builds envelopes from ToolMessages
- `@/lib/supabase`, `@/lib/langgraph` (`createBrowserDeps` production wiring)
- `healthcare_rag/agent/perimeter.py` — the contract `coachProtocol.ts`/`coachApi.ts` mirror

### External
- `@langchain/langgraph-sdk` (`Client`, stream types), `zod` 4

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
