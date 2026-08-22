# Learnings — coach-agent-platform

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## Task 1 — coach state and gate

- The D5 mixed-token rule must inspect NFC-normalized raw source text, not tokenized text: punctuation between a drug and dosage intentionally breaks the conservative association grammar.
- `CoachSafetyGate` must be instantiated per turn because `SafetyGate.assess()` reconstructs `SafetyAssessment`; classifier failure therefore rides the instance attribute and remains isolated under concurrent calls.
- D0a and D0b are feature-only fast paths. D0a clears the untracked wake in its routing update after validating the store record and thread/token chain; D0b deliberately omits `attachment_id` from the gate update.
- Explicit input/output schemas belong on `StateGraph`; the compiled coach graph returns only `messages` and `follow_ups`, while all three inbound raw channels use `UntrackedValue`.
- The ordered D5 matrix and lexicons live in `agent/features.py`; `agent/gate.py` re-exports `compute_features` while retaining routing/classifier responsibility and staying below the pure-LOC ceiling.

## Task 8 — Route-A relay

- A child compiled with `checkpointer=True` and invoked with the unchanged parent `RunnableConfig` receives a nested checkpoint namespace: the two-turn acceptance test retained inner history for one coach thread while a second parent thread began empty.
- The relay normalizes exactly the four public `GraphOutput` fields before assembly. Informational answers receive deterministic framing and bullets, while short-circuited responses bypass framing and preserve refusal bytes.
- The `pipeline` fallback must create both the `InMemorySaver` and UUID thread inside the node on every invocation; tests assert two turns each observe an empty fake-child history.

## Task 3 — auth-scoped long-term memory

- The process-owned privacy scanner in `graph.resources` is the shared seam for memory writers: first scan supplies the scrubbed value, and a second scan of that value must report zero kinds before persistence.
- LangGraph custom auth places the principal at `configurable.langgraph_auth_user`; only its `identity` field determines the `("users", identity, kind)` namespace. Tool/model `user_id` input is discarded.
- Platform semantic search is enabled additively under `store.index` in `langgraph.json`; offline tests use an unindexed `InMemoryStore`, so no embedding request is made.

## Task 2 — user store policy boundary

- Schedule state remains purely event-derived: approval events are folded in `(created_ts, event_key)` order, and equal timestamps therefore deterministically apply the maximum event key last.
- `event_key_for` hashes the UTF-8 user id, one NUL separator byte, and the UTF-8 op id; no clock value enters identity, so crash replay can look up before append.
- All ordinary mutation helpers share the erasure-gate check and existing `PrivacySanitizer.scan()` policy; model payloads are revalidated after scrubbing so bounded fields remain valid after replacement expansion.
- BaseStore searches are prefix-based and truncate silently, so event reads, reminder/log lists, namespace discovery, and erasure all paginate explicitly. Record deletion pages repeatedly from offset zero to avoid skipping rows as the namespace shrinks.
- Upload registry expiry is enforced on read in code without refresh, while the registry remains part of the same owner namespace and privileged erasure sweep.

## Task 10 — Agent Server perimeter

- LangGraph custom auth and the Starlette middleware must enforce the same principal boundary: auth filters constrain native persistence operations, while middleware validates exact member envelopes and projects responses before bytes leave the server.
- Upload idempotency needs an atomic deterministic reservation write before extraction. Concurrent duplicate requests then return one creation and one in-progress/done response while making exactly one extraction call.
- Thread deletion is a saga rather than one transaction: write an owner-scoped cleanup marker first, pause reminders and delete known/discovered crons, permit native deletion only after cleanup succeeds, and clear the marker only after a follow-up read proves the thread is gone.
- The hermetic acceptance fixture can exercise a real ephemeral `langgraph dev` process without external traffic by substituting local OpenAI, Supabase, and LangSmith endpoints and injecting a child-process socket guard.

## Task 7 — schedule-change interrupt

- A real `ToolNode` in a tiny checkpointer-backed StateGraph is the honest interrupt test seam: the resumed tool restarts from its first line, reloads the pending OpRecord, and receives the prior `interrupt()` result through `Command(resume=...)`.
- The persisted OpRecord is both the idempotency boundary and the privacy boundary. Loading it before target resolution prevents selector drift on resume, and reloading immediately after `put_op_if_absent` ensures the surfaced CalendarChangeCard bytes are the scrubbed stored bytes.
- Fresh decisions fold the returned ApprovalEvent into the already-read state in memory; crash replay instead finds the event first and can safely rebuild the snapshot from the durable fold before terminalizing the op.
- LangChain's custom `args_schema` receives the injected `runtime` key before Pydantic parsing in the locked version, so the wrapper schema must allow that injected extra while every action model independently remains `extra="forbid"`.

## Task 6 — view_schedule tool

- MiniCalendar.d.ts pins firstWeekday as 0=Sunday while `calendar.monthrange` returns 0=Monday; the tool converts with `(+1) % 7`. The `.prompt.md` example (June 2026, firstWeekday={0}) contradicts its own `.d.ts` — the `.d.ts` comment is normative.
- `calendar.month_name`/`strftime("%B")` are locale-dependent at access time, so monthLabel uses a fixed English month-name tuple for deterministic envelopes.
- Entry ids belong only in the envelope text (`entry_id | date | time | kind | note`) — the MiniCalendar data contract carries none — and the fold already returns scrubbed strings, so the read path never re-scans (parse-once at the append_event boundary).
- The Route-B tool pattern is `tool("name")(impl)` with `config: RunnableConfig` + `store: Annotated[BaseStore, InjectedStore()]` parameters; the wire schema then exposes only the model-facing args, and tests drive `*_impl` directly with a config dict.

## Task 5 — log_injection week-strip tool

- `tool(...)` over a signature with `Annotated[BaseStore, InjectedStore()]` leaves the unserializable store param inside `args_schema` (plain `args_schema.model_json_schema()` raises `PydanticInvalidForJsonSchema`); passing an explicit `args_schema=LogInjectionArgs` model both cleans the model-facing schema and keeps signature-based store/config injection working.
- The sparse week-strip is date-keyed by construction: today is always the single `logged` entry, `upcoming` days are one-per-distinct-future-dose-date from `list_schedule`, and `nextDoseLabel` (full weekday name) comes from `next_dose(from_date=today)` — same `dose|injection` kind filter as todo 2's reader.
- Shared Wave-3 conventions that keep the tools composable: `*_impl` + `today: Callable[[], date] | None` provider param, module-level `STORE_REFUSAL`/`PRIVACY_REFUSAL`, raising frozen-dataclass `*IdentityError`/`*ScopeError`, and the scrub seam `get_resources().privacy` (monkeypatchable at module scope, same as todo 3/4).
- TypedDict + `TypeAdapter(...).validate_json` is the repo's typed envelope-parse pattern for tests; `.get("nextDoseLabel")` avoids `reportTypedDictNotRequiredAccess` on the optional key.

## Task 4 — log_metric tool

- The model-facing tool schema contract lives on `tool.tool_call_schema` (or `.args`, properties only), NOT on `args_schema.model_json_schema()` — the raw args_schema keeps injected fields and pydantic cannot render `Annotated[BaseStore, InjectedStore()]` as JSON schema, while `args`/`tool_call_schema` correctly exclude `RunnableConfig` and `InjectedStore` params. `TypeBaseModel` unions the pydantic V1 model, so basedpyright needs `langchain_core.utils.pydantic.model_json_schema(model)` to access the schema of a `tool_call_schema`.
- Envelope data dicts typed `dict[str, JsonValue]` must be built by assigning into an empty annotated dict (and `list[JsonValue]` by appending floats); a dict/list display infers `dict[str, str | list[float]]` which basedpyright rejects against the recursive JsonValue alias under invariance.
- A Fake scanner passed to store_data's `scanner: PrivacyScanner | None` must name its protocol parameter `text` (protocol matching is name-sensitive); `value` silently fails basedpyright.
- `uv run pytest` (console script) cannot import `tests.graph.conftest` (`from tests...` needs CWD on sys.path); use `uv run python -m pytest` or `make test`.
- Metric trend conventions pinned for siblings: label map {weight→Weight, waist→Waist, bmi→BMI}, value `f"{value:g}"`, delta `f"{delta:+.1f} {unit}"` vs the most recent prior entry, deltaGood = delta ≤ 0 (all three metrics are lower-is-better), points = prior[-(8-1):] + new value ascending, block_id `trend:<metric>`.

## Task 12 — frontend scaffold (conventions for todos 13/14/15)

- Runner is bun: `bun --cwd frontend run {dev,build,test}`. Next 16.3.2 App Router, TS strict + noUncheckedIndexedAccess, src dir, no Tailwind, no ESLint (quality gates are `next build` type-check + vitest). `frontend/AGENTS.md` is next-generated — commit it or it keeps reappearing as a dirty file.
- Design assets live VERBATIM under `frontend/src/design/` (tokens, base, components/components.css, styles.css — byte-identical copies; never edit, `cmp`-verified). `src/app/globals.css` is a single `@import "../design/styles.css"`. Google Fonts ride typography.css's remote @import (works in the built CSS chunk).
- Wordmark policy: plain "nymble" in `var(--font-headline)` + `var(--rust)`; no logo, no invented icons. Kit line-glyph SVGs are ported inside the fixed-contract cards only.
- The catalog wire grammar is TWO zod layers: `WireSchemas` (fact props = `DataRefSchema` `{__ref:{turn_scope_id,block_id,pointer}}`, literals rejected) then `ConcreteSchemas` (hydrated values) re-validated at the render boundary. `resolveCatalogTree(tree, envelopes, turnScopeId)` is pure and returns `{roots, elements, events}` — reuse it in todo 13 to render compose_ui trees; `<CatalogTree/>` wraps it with `DispatchProvider` + json-render `JSONUIProvider`/`Renderer`.
- Fact vs static-copy classification (mirror this in the todo-16 backend model): InjectionTracker {medicationName,doseLabel,days,nextDoseLabel}, MiniCalendar {monthLabel,firstWeekday,daysInMonth,highlights}, TrendCard {label,value,unit,delta,deltaGood,points}, ActionCard {title,body}, StatRow {stats}, ScoreRing {score}, Timeline {items} are ref-only; ActionCard/Button labels, Tag/Label/Card text, ScoreRing label, variants/enums/dispatch ids stay literal (backend static-copy allow-list owns those).
- `defineCatalog(schema, {components, actions: {}})` from @json-render/react/schema + @json-render/core: the empty `actions` object is REQUIRED for type inference — omitting it widens component props to `unknown` in `defineRegistry`.
- The json-render Renderer does NOT zod-validate props at render; all validation is ours (`render.tsx`). Renderer also requires `JSONUIProvider` (or State+Action+Visibility providers) or it throws `useVisibility must be used within a VisibilityProvider`.
- Sparse-to-seven adapter (`catalog/weekstrip.ts`): anchor = latest entry date (or explicit); Monday-first window; same-date duplicate = later entry wins; same-weekday from another week drops. InjectionTracker status union adds `muted` (rendered at 0.35 opacity).
- Fixed dispatch map (`catalog/dispatch.tsx`): log_weight, log_injection, view_schedule, change_schedule, set_reminder, cancel_reminder, upload_document, confirm, decline. Unknown id -> nothing + telemetry; known-but-unregistered -> no-op + telemetry. Handlers ride `DispatchProvider`; todo 13 registers the real ones.
- Telemetry is spy-able via `telemetrySink.emit = fn` (object property, no ESM tricks).
- SDK: `new Client({apiUrl: NEXT_PUBLIC_LANGGRAPH_URL ?? http://localhost:2024, apiKey: null, onRequest: bearerRequestHook(getAccessToken)})` — `apiKey: null` prevents client-side env auto-load; the async onRequest hook is the refresh-aware bearer seam. `supabaseAccessToken()` refreshes when <60s to expiry.
- Supabase client is LAZY (`getSupabase()` throws an actionable message only on use) so builds/tests never crash on missing env; `LoginForm` takes an injectable `client` prop for tests.
- Testing: vitest jsdom with `globals: true` (required for RTL auto-cleanup), alias `@` -> src, tests colocated in `__tests__/`. Machine style assertions grep rendered style attrs/classNames + the verbatim token files (Playwright screenshots come in todo 15).
- `next.config.ts` pins `turbopack.root` to frontend/ so builds never chase a lockfile outside the repo.

## Task 18 — document claim and HITL memory review

- The upload registry remains readable until `claim_document` has durably inserted the deterministic SHA-256 `OpRecord`; only `review_document` marks the registry consumed, so a crash before claim commit can safely retry without losing the proposal.
- Persisting the scrubbed `interrupt_payload` inside the op record before the sole `interrupt()` makes restart and post-consumption replay independent of the ephemeral upload registry.
- Accepted edits are sanitized field-by-field through the shared memory policy. A rejected field is represented in the result with a privacy notice while clean sibling fields still persist under deterministic profile keys.
- `put_op_if_absent` and `put_op` must preserve the opaque SHA-256 op key while sanitizing record content; treating the key as ordinary text changes identity and breaks replay lookup.

## Task 19 — reminder tools and wake delivery

- Cron registration is a two-system saga: write a pending inactive owner-scoped reminder first, then create the remote cron, and activate only after its id is durable. Ambiguous HTTP outcomes must reconcile by metadata before retrying so one logical reminder cannot create duplicate schedules.
- `store_data.edit_reminder` rotates wake tokens, so finalizing a create must explicitly preserve the token embedded in the newly registered cron payload; an ordinary edit during finalization invalidates the first wake immediately.
- The gate clears the public `cron_wake` input but can hand the validated capability to a model-free node through an `UntrackedValue` state channel. The delivery node revalidates the owner/thread/token chain and clears that channel after emitting its envelope.
- Cron-triggered runs have no human message id, so the generated AIMessage id is the stable turn scope for the ReminderCard DATA envelope.

## Task 13 — coach chat UI (streaming + action-bar conventions for todos 14/15)

- The LangGraph JS SDK's `threads.create()` ALWAYS sends `{"metadata":{}}` (spread-of-undefined leaves an empty object, not an omitted key) — the perimeter's `body == {}` rejects it. Thread creation must be raw fetch; `runs.stream` IS safe: `JSON.stringify` drops the undefined keys the SDK stuffs into its json object, leaving exactly the fixed envelope.
- Assert request bodies against the REAL SDK: `new Client({callerOptions: {fetch}})` + `onRequest` capture beats mocking the SDK — it pins the wire bytes including the SSE parse (the parser rejects `data: [DONE]`; real streams just end).
- Human messages are LOCAL: filter `type === "human"` out of stream updates (the gate's scrubbed HumanMessage still arrives in latest-state reads) and append the local echo as a wire-shaped `{type:"human", id:"local-*"}` message into the ordered store — a parallel echoes array breaks turn ordering because streamed ai messages land in a prefix turn with no human.
- finalize_coach re-projects the WHOLE messages channel, so the chat store must merge-by-id (id ?? tool_call_id ?? type+content hash) or every finalize duplicates the transcript.
- Async loops inside hooks must not read state through a ref that only updates on render (React batches; the loop outruns the re-render) — thread a local `state` variable through the loop and mirror it into setState.
- Turn window = HumanMessage-bounded; regenerate eligibility AND the resent question both come from the LATEST window (`turnQuestion`), never the global last user message. Tool call OR ToolMessage OR marker OR sentinel question OR pending interrupt OR ready attachment in the window disqualifies.
- The erase marker is `{type:"ai", name:"erase_confirmation_v1"}`; detection after a clean stream EOF and on latest-state reads share one `maybeStartErase` seam, which makes reconnect resume (mount-time state read) free.
- Attachment flow: client-generated upload_id (crypto.randomUUID) → POST multipart → poll status; stage transitions come ONLY from server responses; the NEXT send swaps its envelope to `{question: SENTINEL, attachment_id}` when the local upload state is staged+done, then consumes it.
- `crypto.randomUUID()` + jsdom + vitest: fine; but `new Promise(executor)()` (stray IIFE parens after the executor) type-checks as "Promise<void> is not callable" and hangs runs — write deferred promises as `await new Promise<void>(resolve => { gate.release = resolve });` with a wrapper object so TS doesn't over-narrow the closure variable to null.
- Kit port: the coach-chat surface classes (sidebar/bubble/composer/action-bar) live in `src/app/chat/chat.css` (verbatim kit styles + a few banner/note additions); icons are the kit's inline line-glyph SVGs ported to `chat/components/Icons.tsx`.

## Task 14 — Gen-UI interrupt cards + reminder modes + composed trees

- The four fixed-contract cards have THREE wire shapes, not two: interrupt payloads (`__interrupt__`), ToolMessage DATA envelopes (calendar-change:<op>, reminders:list), and code-assembled AI-message content — reminder_delivery is `"literal\n{envelope}"` (block `reminder:<id>`) and review_document's confirmation is pure JSON `{"component":"MemoryExtractionCard","data":{...}}`. Parse all three in chat/model.ts; `aiDisplayText` is the single seam that guarantees card JSON never reaches a bubble.
- The reminders:list compact items are `{reminder_id, title, scheduleLabel, nextRun?, active}` (reminders.py `_listing_data`) — scheduleLabel, NOT the ReminderCard prop name `schedule`; the delivery envelope card instead uses `schedule`. Map at the render boundary; key React lists by reminder_id.
- ReminderCard full-mode actions and composed-tree Button dispatches are ALL the same mechanism: natural-language NEW runs through chat.send (e.g. "Pause my Weekly weight log reminder" — title rides the phrasing because edit_reminder resolves deterministic title selectors). Never cron/store APIs.
- MemoryExtractionCard's resolved mode is a card-layer prop (`resolvedFields`), not a second component: same rows, per-field "✓ Saved"/"Discarded" trailing status, privacy notice under the value, pencil/buttons hidden. The design-system kit has no resolved variant — the port extends minimally.
- Failed-resume recovery: runStream returns success and approveInterrupt restores the captured interrupt value from a ref on failure ("error toast + card re-enabled once"). Mirroring pendingInterrupt in a ref (assigned during render) is what makes the capture race-free; commitPendingInterrupt mirrors the other state writers (loadThreadState/newConversation/erase).
- bun 1.3.14 rejects `bun --cwd frontend run <script>` (prints the run help, exit 0!) — use a frontend workdir. Silent exit 0 on a failed flag parse is a verification trap.
- Duplicate-click no-op on interrupt cards is structural, not just busyRef: pendingInterrupt clears in the same tick, so the card unmounts on the next flush — a test that expects to click Decline after Confirm cannot (the button is gone), which IS the invariant.

## Task 9 — Route-B agent wiring

- `ModelRequest.runtime` exposes static agent context rather than the invocation `RunnableConfig`; Route B passes an `AgentContext` carrying the authenticated user, parent thread, and server-derived human message id so memory lookup and turn-scope hashing never trust model input.
- `wrap_model_call` supports async wrappers at runtime, but the installed type overloads only describe synchronous decorators. A typed `AgentMiddleware.awrap_model_call` subclass avoids suppressions while preserving async retry behavior.
- Invalid compose calls must retain their AI-message id when rewritten to `{tree: []}` so checkpoint merging replaces the unsafe call in place. The terminal retry-cap fallback needs a fresh id so a provider that repeats message ids cannot leave the correlated error ToolMessage as the final item.
- LangChain middleware state schemas are invariant even though `ToolCallLimitState` extends agent state. A narrow cast at the `create_agent` boundary documents this upstream typing limitation; runtime acceptance proves the built-in limiter still emits one interrupt for a parallel batch.

## Task 11 — deployment configuration and deployed smoke

- Startup validates the configured LangSmith feedback project through a read-only `Client.read_project(project_id=...)` probe; provisioning remains an explicit create-if-absent script so deployment startup never mutates control-plane state.
- A server-derived `/coach/internal/version` route lets the deployed smoke reject local `langgraph dev` and incompatible Agent Server images before exercising any destructive checks; the perimeter admits only authenticated internal principals to that route.
- The deployed smoke owns all ten acceptance checks in one executable script, requires explicit deployment/member/internal credentials, and defaults to HTTPS deployment URLs. A future Make target should invoke `uv run python scripts/deployed_smoke.py --url "$$LANGGRAPH_DEPLOYMENT_URL"` without duplicating the test logic.

## Task 16 — in-process eval parity

- The production coach graph can be evaluated offline by compiling `build_coach_graph()` with isolated `InMemorySaver`/`InMemoryStore` resources and replacing only the gateway, retrieval callable, outer classifier, and Route-B model node.
- Route-A attribution must use checkpoint diffs plus `lineage_leaves`; both normal completion and the inner refusal-finalize short circuit produce one correlated leaf, while all non-A routes produce none.
- Retrieval evidence is accepted only after mapping `(source_name, metadata["id_"])` back to checked-in chunk JSON. Unknown sources, missing ids, and unknown ids are hard failures rather than partial-credit omissions.
- Current-checkout baseline and candidate reports make the gate self-contained: chunk recall tolerates 0.02, medians of three judge samples tolerate 0.05, and safety metrics are monotone.
