# Issues — coach-agent-platform

Problems and gotchas encountered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## Task 1 — verification gotchas

- `scripts/langgraph_smoke.py` requires `make dev` on port 2024. The first direct invocation failed with `ConnectError`; after starting the local Agent Server, all five smoke checks passed.
- CodeGraph auto-sync and one LSP snapshot were stale while new files were being created. Direct `basedpyright healthcare_rag/agent` reported zero errors; subsequent source diagnostics were clean except the stale transient missing-import snapshot.
- Ruff warns that the programming-skill-only `ANYIO_OK`/`SIZE_OK` directives are not Ruff codes, but Ruff exits successfully. The broad classifier catch is required by the fail-closed contract and is separately acknowledged by both BLE001 and the no-excuse checker marker.

## Task 8 — verification gotchas

- `make test` reached 685 passing tests but two concurrently introduced, out-of-scope agent tests failed: `test_langgraph_config_mounts_fail_closed_http_app` found no `auth` key in `langgraph.json`, and `test_make_envelope_has_stable_turn_scope` expected `store_data.TypeAdapter`. Neither failing path is touched by Route A.
- The editor LSP retained a stale pre-edit `RAGChild` diagnostic after the protocol had been removed. Direct `uv run basedpyright` on the changed Route-A files reported zero errors and is the authoritative fresh check.
- A post-commit retry after the sibling wave finished was fully green: `make test` reported 689 passed and 1 deselected. The two failures above were confirmed as transient partial-worktree failures, not residual blockers.

## Task 3 — memory verification gotchas

- `BaseStore` exposes no common infrastructure exception for failed writes. `remember_fact` therefore catches the store boundary broadly, returns a fixed raw-free failure string, and never logs the fact.
- The LangChain tool schema accepts an optional caller `user_id` solely to prove it has no authority; the function deletes it before deriving the namespace from authenticated config.

## Task 2 — verification notes

- The plan pins the models, fold, ledger, all collection helpers, and privileged erasure into the single `healthcare_rag/agent/store_data.py` policy boundary. That mandated module is 654 pure LOC, and its acceptance matrix is 442 pure LOC; splitting either would conflict with the specified file contract in this task.
- Basedpyright reports zero errors and eight intentional warnings: exhaustive `assert_never` branches, private InMemoryStore metadata access used only to force equal-timestamp tests, and private erasure-capability access used only by its security test.

## Task 10 — verification notes

- The long-lived editor LSP retained a stale `Import ".cleanup" could not be resolved` diagnostic after the new module existed. Fresh `uv run basedpyright healthcare_rag/agent/cleanup.py healthcare_rag/agent/perimeter_middleware.py` reported zero errors, and both focused and full runtime suites imported the module successfully.
- Counting extraction calls in the local HTTP stub must update `type(self).extraction_calls`; assigning through the handler instance shadows the class counter and falsely reports zero under concurrent upload tests.

## Task 7 — verification notes

- The editor LSP briefly reported the new `_change_schedule_contract` relative import as missing while direct `uv run basedpyright` resolved both modules with zero errors; this matches the previously observed new-file index lag.
- `tests/agent/test_tool_change_schedule.py` is 549 pure LOC because the plan explicitly requires the full v19 matrix in that one named acceptance file. Production was split by responsibility and remains below the 250 pure-LOC ceiling.
- The first full-suite run had four failures in concurrently introduced document/upload tests, while the focused schedule/store suites had 29 passes. The failures were isolated to sibling Wave-3 files and were not modified by task 7.

## Task 6 — verification notes

- The Wave-3 siblings (todos 4/5/7/18) were editing their own test files while this task verified, so bare `make test` failed only inside `test_tool_log_{metric,injection}.py`, `test_tool_change_schedule.py`, and `test_documents.py` (stub NotImplementedErrors and one mid-edit collection error). None import view_schedule; excluding exactly those four files the full suite passed 729/729. Re-run `make test` after the wave lands.
- `uv run pytest` (without `python -m`) drops CWD from sys.path and breaks `tests.graph.conftest` imports; always use `uv run python -m pytest` in this repo.
- basedpyright's default diagnostics flag cross-module private imports (`_authenticated_user_id` from memory.py), so view_schedule carries its own principal parse raising the public `MemoryIdentityError` rather than importing the private helper.

## Task 5 — verification notes

- `make test` had 4 failures in `tests/agent/test_documents.py` (starlette `NotImplementedError` in authentication) caused by concurrently-modified sibling files (`documents.py`, `gate.py`, `build.py`, `test_documents.py`); stashing my two files reproduced the same 4 failures, proving they are pre-existing and out of scope. All 780 other tests pass, including my 14.
- Wave-3 siblings share this worktree and land commits concurrently (`view_schedule` and `log_metric` appeared mid-task); commit must use a strict pathspec of only my two files.
- memory.py's own `remember_fact.args_schema` cannot JSON-serialize (InjectedStore annotation); the explicit `args_schema=` pattern is the fix for tools whose schema todo 9 will publish.

## Task 4 — verification gotchas

- `make test` during the wave: 4 failures confined to tests/agent/test_documents.py (starlette authentication identity NotImplementedError) from concurrently modified, uncommitted sibling files (documents.py/uploads.py/gate.py/build.py). Stashing those tracked edits made the 4 tests pass; re-running with them deselected gives 784 passed. Same transient partial-worktree pattern as Task 8 — re-run `make test` after the todo-10/18 wave lands.
- A transient `documents.py ↔ uploads.py` circular import appeared and disappeared while siblings were saving intermediate states; importing `healthcare_rag.agent.*` mid-wave can fail for reasons unrelated to one's own files.
- `healthcare_rag/agent/tools/__init__.py` already existed (sibling-created, docstring-only) — this task left it untouched and committed only its own two files, so the intermediate commit lacks the package marker until the sibling commits it.

## Task 12 — frontend gotchas

- A stale `next start` server keeps serving old chunk hashes after a rebuild and 404s its own CSS; kill it before re-verifying a fresh build.
- `Math.max(...arr, NaN)` is always NaN — the weekstrip anchor reduce seeded with NaN muted every slot; guard with `entries.length > 0` first.
- bun-installed `@json-render/react` re-exports `Spec` but not `UIElement`; import the type from `@json-render/core` (added as an explicit dependency since we import it directly).
- zod v4 array `.length(7)` exists and is used for the seven-slot strip (concrete schema), so a malformed adapter output would fail closed at the render boundary rather than render a broken strip.
- The compose_ui wire tree is an ARRAY of nodes; `CatalogTree` accepts either a single node or an array and renders one `<Renderer>` per surviving root against a shared flat elements map (json-render specs have a single root).
- vitest `new URL(..., import.meta.url)` is http-scheme under vite — read fixture files via `resolve(process.cwd(), "src/...")` instead.

## Task 18 — verification notes

- The editor LSP retained a stale missing-export/import-cycle snapshot after `authenticated_user_id` became public and the reservation helpers moved; fresh direct basedpyright reported zero errors and the focused/full runtime suites imported the modules successfully.
- The auxiliary no-excuse audit reports `documents.py` at 375 pure LOC and the plan-mandated `store_data.py` at 670 pure LOC, plus existing bare `ValueError` sites in that policy module. Splitting those specified task boundaries or migrating unrelated existing errors was intentionally left outside this behavior change.
- Final verification was green: the focused matrix reported 86 passes, and `make test` reported 788 passes with one deselection.
  - Resolution: after the todo-10/18 wave landed (a partial-write IndentationError in test_documents.py was observed mid-save), a final `make test` reported 788 passed, 1 deselected — fully green with this task's commit in place.

## Task 19 — verification notes

- Ruff warns that `# noqa: SIZE_OK` is not a Ruff rule code, while the repository's auxiliary no-excuse checker requires that marker for the plan-mandated reminder implementation and acceptance matrix boundaries; Ruff still exits successfully.
- Fresh direct basedpyright reported zero errors and 18 warnings from missing/partial LangGraph stubs and JSON/framework boundaries. Long-lived editor diagnostics for newly added modules were stale, while runtime imports and the full suite were green.
- Final verification was green: the focused reminder/gate matrix reported 60 passes, and `make test` reported 804 passed, 1 deselected, 4 warnings.

## Task 13 — verification notes

- The actionBar "waits for terminal" test cannot observe the busy wait with instant poll resolutions; make the FIRST getThread status poll block on a deferred the test releases manually (wrapper-object pattern, or TS narrows the closure variable to null/never).
- Component-level stream fakes: `fakeStream` records the envelope payload per call — regenerate/branch/feedback assertions read `stream.calls[n].payload` instead of scraping fetch bodies; the WIRE shapes are separately pinned in protocol.test.ts through the real SDK.
- On mount the hook selects the newest thread from threads/search; a test that expects the SENT turn to live in a freshly created thread must first press "New conversation" (or assert against the mount-selected thread) — the mount-selected thread silently becomes the send target otherwise.
- userEvent.upload works on the hidden attach inputs (jsdom) — no need to expose them; opener → input.click() is asserted via a click spy on the testid'd input.
- Erase fixture at component level: marker-bearing thread is the mount-selected thread (t-1), so the delete order fixture is [other..., current-last] = ["t-2","t-1"]; the pure erase.test.ts pins the general ordering (snapshot asc, current last, FAIL-STOP preserves current).

## Task 14 — verification notes

- Todo 13 had already landed part of the interrupt wiring (InterruptPanel, calendar envelope card, upload-driven DocumentIngestCard, CatalogTree in MessageList with handlers={}); the real gaps were the reminders:list schema mismatch (every item dropped — `schedule` vs the backend's `scheduleLabel`), ReminderCard full mode (absent), the memory-confirmation resolved state (absent), composed-tree dispatch (unwired), and failed-resume card recovery (absent).
- The sibling todo-9 wave keeps healthcare_rag/agent/* + tests/agent/* dirty in the shared worktree; commit used a strict frontend/ pathspec. Backend contracts were read from the working-tree files and matched the documented plan contract.
- `bun --cwd frontend run test/build` exits 0 while printing only the bun run help (flag not accepted before the subcommand) — evidence commands must run with workdir frontend/.
- Compact ReminderCard renders an inert Toggle (no handler) because the ported kit card always draws it in compact mode; per the read-only contract no onToggle is ever passed. If QA flags the affordance, a disabled visual state needs a kit-level change (out of scope).

## Task 9 — verification notes

- CodeGraph auto-sync was disabled by a stale file lock during the final pass, so current edited files were verified through direct reads, LSP diagnostics, fresh basedpyright, and runtime tests rather than the frozen index.
- Fresh basedpyright reported zero errors and 20 warnings from LangChain's partially unknown `create_agent` generic signature, Pydantic model metadata, exhaustive match branches, and framework JSON boundaries.
- Final verification was green: the focused Route-B suite reported 10 passes, the agent suite 312 passes, `make test` 814 passes with one deselection, and the offline coach smoke exited successfully.

## Task 11 — verification notes

- The hermetic Agent Server fixture needed a read-only `/feedback?` response because startup now probes the configured feedback project before accepting traffic; this keeps tests offline while exercising the real lifespan path.
- The acceptance-plan-mandated `scripts/deployed_smoke.py` is 873 pure LOC and intentionally carries all ten deployed checks in one executable. Ruff reports the repository audit marker `# noqa: SIZE_OK` as an invalid Ruff directive but exits successfully; the marker is retained for the no-excuse size audit convention.
- No live deployment was invoked. Offline verification passed with 11 focused deploy tests, 33 perimeter/deploy tests, and the full suite at 837 passed with one deselection; the real ten-check deployed run is deferred to F3.

## Task 16 — verification notes

- CodeGraph auto-sync was disabled by a stale lock and the editor LSP briefly retained pre-edit diagnostics. Fresh direct Ruff, basedpyright, focused tests, both CLI runners, and the full suite were authoritative.
- Direct basedpyright reported zero errors and framework-boundary warnings from LangGraph's partially typed compiled graph/message output and the deliberate test-time replacement of the production model node.
- Final verification was green: 14 focused tests, `PARITY PASS`, a corrupted candidate failed specifically on `chunk_recall`, the CoachState telemetry guard passed, and `make test` reported 839 passed with one deselection.
