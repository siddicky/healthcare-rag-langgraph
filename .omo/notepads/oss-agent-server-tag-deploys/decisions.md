# Decisions — oss-agent-server-tag-deploys

Architectural choices and rationales discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## 2026-08-22 — todo 1: readiness registry + graph storage attachment

### Readiness registry API

`server/app.py:ReadinessState`

```python
readiness = ReadinessState()
readiness.register("config")        # idempotent, initially False
readiness.register("graphs")
readiness.set_ready("config")       # marks True
readiness.set_not_ready("name")     # marks False (optional)
readiness.is_ready()                # True iff every registered check is True; False if none registered
readiness.checks                    # copy of dict[str,bool] for inspection
```

Lifespan creates `app.state.readiness` and `app.state.config` before any I/O.
Future todos register via `app.state.readiness.register("auth")` / `register("scheduler")` / `register("custom_app")` inside lifespan (or synchronously in `create_app` before lifespan). They call `set_ready("auth")` once their subsystem is up. `/ok` returns 503 until `is_ready()` is True, 200 `{"ok":true}` after. No hard-coded subsystem count or order beyond "config/graphs first"; downstream code just registers extra names.

### Graph storage attachment mechanism

`server/graphs.py:load_raw_graphs` + `attach_graphs`

- `load_raw_graphs` resolves each `langgraph.json:graphs` entry (`"./path:attr"`) via `importlib.util.spec_from_file_location` + `getattr`.
- `attach_graphs(raw, storage)` recompiles via `raw_graph.builder.compile(checkpointer=storage.saver, store=storage.store, name=name)`. Both `healthcare_rag` (`build_graph().compile(...).builder`) and `coach` (`build_coach_graph().compile(...).builder`) expose `.builder` (a `StateGraph`) so recompilation satisfies coach's store-requiring nodes and `interrupt()` support.
- `server/storage.py:create_storage` returns `Storage(saver=InMemorySaver(), store=InMemoryStore(index=...), threads={}, runs={}, crons={})` — single seam for all server state. Lifespan calls `await saver.asetup()` / `store` setup if present before marking ready.

### Repo facts corrected

- `pyproject.toml` include was `["healthcare_rag*", "evals*"]` as plan stated; updated to `["healthcare_rag*", "evals*", "server*"]`.
- `uv.lock` before bump had `starlette 1.6.0`, `langgraph-api 0.13.0`; after adding `starlette>=0.40,<1` it resolved to `0.52.1` and `langgraph-api 0.9.1` (consistent with `langgraph.json` `api_version 0.12.6` image expectations). `croniter` resolved to `3.0.4` (was 6.2.4 pre-lock). Added `tzdata 2026.3`.
- `InMemorySaver` has no `asetup`/`setup` in current `langgraph 1.x` — lifespan guards with `hasattr` so it is no-op.
- `InMemoryStore` index requires `embed` key; when `OPENAI_API_KEY` absent the constructor raises; `create_storage` falls back to `InMemoryStore(index=None)` to keep scaffold testable offline.

## 2026-08-22 — fix: langgraph-api 0.13 pin (post-review)

**Problem:** Adding `starlette>=0.40,<1` (verbatim from plan) conflicted with `langgraph-api 0.13.0`'s `starlette>=1.3.1` — resolver downgraded `langgraph-api 0.13.0 → 0.9.1` (which allows `starlette>=0.38.6`) and `starlette 1.6.0 → 0.52.1`, `grpcio 1.81.1 → 1.80.0`. Verified via `pypi.org/pypi/langgraph-api/0.13.0/json` `requires_dist` and `uv tree` before fix.

**Fix path 1 (constraint) + minimal relaxation:**
- Relaxed `pyproject.toml` `starlette>=0.40,<1` → `starlette>=0.40` (no upper cap) — the `<1` was the sole blocker; widening to uncap restores 0.13 compatibility without changing the lower bound the plan mandated.
- Added `[tool.uv] constraint-dependencies = ["langgraph-api>=0.13,<0.14"]` to pin the dev-served API version on the plan's cited evidence (both dual-review rounds verified 0.13.0 via `uv tree`).

**Proof after fix:**
```
$ uv lock
Updated langgraph-api v0.9.1 -> v0.13.0
Updated starlette v0.52.1 -> v1.6.0
Updated grpcio v1.80.0 -> v1.81.1
$ uv run python -c "import langgraph_api; print(langgraph_api.__version__)"
0.13.0
$ uv run pytest tests/server/test_scaffold.py -q
5 passed in 5.16s
$ uv run pytest --collect-only -q
1653/1654 tests collected (1 deselected)
```

Alternative considered (option 2: loosen `langsmith`/`langgraph-sdk`) was unnecessary — the conflict was entirely `starlette`'s upper bound, not those deps. Todo 8's pinned-oracle venv should inherit this constraint or rely on the same `tool.uv` section.

## 2026-08-22 — todo 10: server image + compose stack + license proof + no-local-dev guarantee

### server/auth.py existence at time of todo 10

- **At start of todo 10 work:** `ls server/auth.py` -> `No such file or directory` (wave 2 parallel, todo 2 not yet merged). Per plan instruction, the `no-credential -> 401` container assertion was **SKIPPED** at that point and noted as follow-up for whoever finishes last among todos 2/4/10.
- **By end of todo 10 work:** `server/auth.py` had landed via parallel execution (9426 bytes, `server/auth.py:1` exports `AuthConfigurationError`, `ScopeUser`, `PUBLIC_PATHS={"/ok","/info"}`, `STUDIO_IDENTITY`). Re-built image (`hc-rag-server:dev`) with latest `server/` contents and **re-tested**: `POST /threads/search` without credential now returns `401 {"detail":"Unauthorized"}` with `SERVER_LOCAL_DEV=0` and `404` (not 401) with `SERVER_LOCAL_DEV=1`, proving the flag OFF in the production image and the Studio mapping when ON.

**Follow-up status:** No follow-up needed — 401 assertion has been proven after parallel landing. Whoever finishes last can simply re-run the same curl; it is now green.

### SBOM / license-proof command (for todo 8 CI to reuse/extend)

Primary probe (also works in CI without extra tooling):
```bash
docker run --rm hc-rag-server:dev python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('langgraph_api') is None else 1)" && echo "langgraph-api: absent"
docker run --rm hc-rag-server:dev pip list | grep -qi langgraph-api && echo "present" || echo "langgraph-api: absent"
docker run --rm hc-rag-server:dev uv pip list | grep -i langgraph
# shows langgraph, langgraph-sdk, etc but NOT langgraph-api
```

Why this works: `pyproject.toml:41 constraint-dependencies = ["langgraph-api>=0.13,<0.14"]` pins the dev-only API package; `server/Dockerfile` builder uses `uv sync --frozen --no-dev`, so the `dev` extra (which is the only path that pulls the Elastic-licensed `langgraph-api`) is excluded. Runtime `COPY --from=builder /opt/venv` therefore never contains it. Verified via `pip list` and `find_spec`.

For the full CI SBOM job (todo 8), extend with `syft` or `docker sbom` if the runner supports it, but the `find_spec` + `pip list` probe is the minimal license-proof gate and is already sufficient to fail closed if the `--no-dev` flag is ever dropped.

### Dockerfile / compose design notes

- **Two-stage mirror:** builder mirrors `Dockerfile:1-46` (uv + `UV_PROJECT_ENVIRONMENT=/opt/venv` + two `uv sync --frozen --no-dev` stages). Runtime mirrors `Dockerfile:28-44` (slim-bookworm, `PRESIDIO_DEVICE=cpu`, non-root 10001, `/app`, `read_only`+`tmpfs` friendly). Extra `COPY --from=uv` in runtime was required to satisfy `spacy`'s "No package installer found" check during the `langgraph.json:31` verify step.
- **langgraph.json parity:** `ENV PRESIDIO_DEVICE=cpu` plus the exact two `RUN` lines from `langgraph.json:30-31` are present verbatim. The `en_core_web_sm` wheel pin `sha256=193242...` is identical. An observed side-effect: the verify step auto-downloads `en_core_web_lg` (382 MiB) via `uv` — not blocked, but noted for image size (~1.88 GB).
- **`COPY langgraph.json + healthcare_rag + server` to `/app`:** Required because `server/graphs.py` loads graphs via `spec_from_file_location("./healthcare_rag/graph/__init__.py")` (file path, not dotted import). Without the source at `/app`, lifespan fails `FileNotFoundError: langgraph.json`. Alternative dotted-import refactor was not pursued to keep the change minimal and reviewable.
- **compose verbatim:** `weaviate` block copied lines 3-29 verbatim (image, ports, volumes, restart, healthcheck, environment). Do not drift; a `diff` check proves identity. `server` service matches spec: `build.context:. dockerfile:server/Dockerfile`, `depends_on healthy`, `env_file .env required:true`, `WEAVIATE_HOST: weaviate`, `8000:8000`, `init:true`, `read_only:true`, `tmpfs:/tmp`.

### Not applicable adversarial classes (one line why)

- **malformed input / prompt injection / cancel-resume / stale state / dirty worktree / flaky tests / repeated interruptions:** Not applicable — this todo is build+compose plumbing, no user input parsing, no interrupt/resume, baseline was clean per todo 1.

## 2026-08-22 — todo 2: Auth SDK dispatch + readiness

### Installed `langgraph_sdk.Auth` introspection contract

The installed SDK exposes no public execution method after decorators register handlers. Its
`Auth.__init__` source explicitly says the following private fields are accessed by the API and
renaming or changing them is breaking:

- Authentication: call `auth._authenticate_handler` with keyword arguments `method`, `path`,
  `headers` (`dict[bytes, bytes]`), and `authorization` (`str | None`). There is exactly one
  registered authenticator.
- Authorization: inspect `auth._handler_cache`, then resolve `auth._handlers` in this order:
  `(resource, action)`, `(resource, "*")`, `("*", action)`, `("*", "*")`; finally use
  `auth._global_handlers[-1]`. Invoke the selected handler as
  `await handler(ctx=ctx, value=value)`. The mutable `value` object is passed through unchanged.
- Result interpretation: `None`/`True` allow, `False` raises 403, and `dict` is the caller's scope
  filter. `server.auth.AuthPolicyEngine.run_policy(resource, action, user, value)` centralizes this
  pattern for todos 3/5/6/7.

This is a private-but-compatibility-promised SDK seam, not a documented public dispatcher. Recheck
these fields on any `langgraph-sdk` upgrade.

### Local-dev fixture and readiness

Todo-2's contract fixture pins the OSS behavior: after a custom-auth 401, `local_dev=True` maps to
`StudioUser("langgraph-studio-user")` whether `x-api-key` is absent or present; malformed opaque
header text is never evaluated. With the flag false, both shapes remain 401. The pinned
`langgraph-api==0.12.6` source itself selects `StudioNoopAuthBackend` only for
`x-auth-scheme: langsmith`; therefore todo 8's live oracle capture should retain this source-level
distinction as an explicit parity-risk check rather than silently re-deriving the OSS rule.

`server.app.create_app()` registers `readiness.register("auth")` immediately after creating the
registry. Its lifespan calls `readiness.set_ready("auth")` after storage/graphs load; importing or
validating `healthcare_rag.agent.auth:auth` fails the app factory instead of producing a partially
ready server.

## 2026-08-22 — todo 2 follow-up: Python 3.11 aliases + auth-before-manifest

- `server/auth.py` must use the repository's Python 3.11-compatible alias convention:
  `Name: TypeAlias = ...` imported from `typing`. PEP 695's `type Name = ...` statement is Python
  3.12-only and violates the repository's `>=3.11` runtime contract even when the development venv
  runs 3.12.
- **Auth-before-manifest ordering is intentional and plan-mandated.** `/ok` and `/info` are the only
  public routes. Every other route authenticates before routing, including unimplemented manifest
  entries, unknown paths, MCP, and A2A. Consequently an unauthenticated `/metrics` request returns
  401 rather than revealing its later 501 classification. Tests for todos 3/5/6/7 must authenticate
  first—using local-dev Studio mode or a controlled stub principal—before asserting endpoint-level
  4xx/5xx behavior. Once authenticated, manifest entries remain 501 and unmounted MCP/A2A remain
  404/405.

## 2026-08-22 — todo 4: checkpointed run engine

### Run record and private runtime state

`Storage.runs[run_id]` keeps the public record shape exactly as
`{run_id, thread_id, assistant_id, input, config, status, created_at, command?}`. `command` is
present only for resume runs; `input` is `null` for those runs. Status is one of `pending`,
`running`, `error`, `success`, `timeout`, or `interrupted`. Executor-only state (completion/event
signals, stream events, output, cancellation scope, and the pre-run checkpoint values) lives in
`RunEngine.runtime`, not in the public registry record.

### Queue bound and multitasking

The bound is **100 pending runs server-wide**, matching the plan literally rather than using a
per-thread interpretation. A running run does not consume the pending quota. Submission number
101 while 100 runs are pending returns 503 with `Retry-After: 1`. Each thread has one active run
and a FIFO deque. `reject` returns 409, `enqueue` appends, and `interrupt` waits for the active run
to become `interrupted` before starting the replacement.

### SSE and rollback assumptions for todo 8 to verify

No pinned oracle fixture existed. The protocol-v2 documentation confirms `text/event-stream`, but
documents the newer `ProtocolEvent` subscription endpoint rather than legacy run-stream bytes.
Therefore run SSE framing was inferred from `scripts/langgraph_smoke.py:179-197`, whose SDK events
have `event.event` as the stream mode and `event.data` as the graph chunk. Implemented bytes are
exactly `event: <updates|custom>\ndata: <compact-json>\n\n`, in the order yielded by
`graph.astream(..., stream_mode=["updates", "custom"])`; no synthetic metadata or terminal event
was invented. Manual capture: `b'event: updates\ndata: {"step":{"value":5}}\n\n'`. Todo 8 must
capture the pinned oracle and correct this if it emits metadata, IDs, spaces, or terminal frames.

Rollback keeps both queued and running records with `status="interrupted"`. Queued rollback has no
checkpoint work because execution never started. Running rollback cancels the stream, then restores
the pre-run snapshot with `aupdate_state`; if the thread had no prior values it deletes the newly
created checkpointer thread. This is the best-available interpretation of
`scripts/langgraph_smoke.py:237-247`; todo 8 must re-verify the precise pinned-oracle checkpoint
history semantics (especially whether restoration creates a new checkpoint or rewinds identity).

## 2026-08-22 — todo 6: in-process cron registry and scheduler

### Best-characterized response schema

No oracle fixtures existed for todo 6. The public response is exactly these fields:
`cron_id`, `thread_id`, `end_time`, `schedule`, `created_at`, `updated_at`, `payload`,
`next_run_date`, `metadata`, `enabled`.

- `schedule` and `next_run_date`: `scripts/deployed_smoke.py:601-605`.
- `enabled`: `scripts/deployed_smoke.py:611-616`.
- `payload.input`: `scripts/deployed_smoke.py:630-638`.
- `cron_id`: `scripts/deployed_smoke.py:657-664`.
- `metadata`: `scripts/deployed_smoke.py:697-701`.
- `thread_id`, `schedule`, `timezone`, `enabled`, `next_run_date`, and `metadata` are the fields
  consumed by `healthcare_rag/agent/cron_client.py:30-39`; timezone is accepted by that consumer but
  is not emitted because the characterized Agent Server cron record shape stores it internally.
- `end_time`, `created_at`, and `updated_at` are retained from the plan's fixture-characterized
  Agent Server record requirement; todo 8 must verify their exact null/timestamp serialization.

Search returns a bare list, not an `{items: ...}` envelope. This is load-bearing because
`CronClient._cron_page` accepts either but `scripts/deployed_smoke.py:581-585` calls `list_body`.
Delete returns 204. Disabled crons return `next_run_date: null`.

### Integration choices for todo 7

Todo 7 should import and call:

```python
start_scheduler(
    engine: RunEngine,
    storage: Storage,
    clock: Callable[[], datetime] | None = None,
) -> asyncio.Task[None]
```

Call it with the exact `app.state.run_engine` and `app.state.storage` instances during lifespan,
then cancel/await the returned task during shutdown. Todo 6 intentionally does not modify
`server/app.py`.

- Registry overflow is **503** with `Retry-After: 1`, matching todo 4's retryable bounded queue
  behavior. The bound is 500 server-wide in-memory records.
- Fired runs force `multitask_strategy="enqueue"`. Evidence: the real reminder client sends enqueue
  in `healthcare_rag/agent/cron_client.py:114-125`, and deployed smoke pins it at
  `scripts/deployed_smoke.py:648-656`; this preserves a pending interrupt instead of rejecting or
  replacing it.
- Thread crons submit to their exact stored thread id. Stateless crons allocate a fresh opaque
  thread id per fire because `RunEngine.submit` requires a thread id while stateless executions must
  not share checkpoint state. This is an assumption for todo 8 to verify against the pinned oracle.
- Five-field cron expressions and IANA zones are evaluated with `croniter` plus `zoneinfo`/`tzdata`.
  `next_run_date` is serialized as an aware UTC ISO timestamp. Todo 8 must verify whether the oracle
  preserves the schedule zone's offset instead.
- The scheduler catches queue-full/conflict outcomes and leaves the cron due for retry on its next
  one-second scan. Todo 8 must verify retry/backoff behavior and whether failed submissions advance
  `next_run_date`.
- Crons are intentionally not persisted; restart wipes the registry.

## 2026-08-22 — todo 3: threads service, TTL, copy, search

### if_exists exact semantics chosen (assumption for todo 8 to verify)

`POST /threads` fields: `thread_id` (optional UUID string), `metadata` (optional dict), `if_exists` (optional), `ttl` (optional `{strategy:"delete", ttl:int minutes}`).

- `if_exists="raise"` (the only value the upload reservation flow uses — `healthcare_rag/agent/uploads.py:78`) → 409 `{"detail":"Thread already exists"}` if a non-expired thread with that id already exists; otherwise create.
- `if_exists="do_nothing"` → return the existing record unchanged (200) if present; otherwise create. Alias `reuse` behaves identically (both accepted, same branch); the plan's phrasing "reuse/do-nothing" suggests the oracle may use either name, so we accept both to avoid a harness failure.
- `if_exists="overwrite"` → replace the existing record's `metadata` (and its spread top-level keys) with the new `metadata`, update `updated_at`, recompute `expires_at` from `ttl` if supplied else clear it; preserve `created_at`. If the thread did not exist, create anew.
- `if_exists=None` with a supplied `thread_id` that already exists → 409 (same as `raise`) — prevents silent overwrite when the caller forgot to specify a mode. This matches the upload reservation expectation (it always sends `raise`).

Invalid `if_exists` → 422, invalid UUID `thread_id` → 422, invalid `ttl` → 422.

No oracle fixtures existed at build time (todo 8 will characterize); these names/semantics are the best judgment from `scripts/deployed_smoke.py`, `scripts/langgraph_smoke.py`, and `healthcare_rag/agent/uploads.py:76-90`. Todo 8 must capture the pinned `langgraph-api==0.12.6` oracle's actual `if_exists` enum strings and exact response codes/shapes and correct this section if they differ (e.g., if the oracle spells `do_nothing` differently or overwrites `created_at`).

### copy semantics chosen (assumption for todo 8 to verify)

`POST /threads/{id}/copy` (member allow-listed via `perimeter.py:149-161`):

- Requires the caller to have `threads.read` scope on the source (same filter as `GET /threads/{id}`) and `threads.create` permission for the new thread. Source scope failure → 404 (not 403), per the same `require_scope_match` hiding.
- Creates a new UUID `thread_id`, `created_at`/`updated_at` = now, `metadata` = deep copy of source `metadata` plus any `threads.create` policy mutation (e.g., member `user_id` re-injection), flattened top-level keys same as create, **without** copying `expires_at` (reservation TTL is not cloned). Returns the new record 200.
- Checkpoints are copied best-effort via `storage.saver.acopy_thread(source, target)` if present (langgraph `InMemorySaver` exposes both sync and async variants); failure is swallowed as the new thread is still usable empty. This matches the real server's thread-copy which clones history. Todo 8 must verify whether the oracle also copies runs/crons or only checkpoints, and whether it preserves `expires_at`.

DELETE cascade verified set in this implementation: thread entry + `await storage.saver.adelete_thread(thread_id)` + `storage.runs` entries where `thread_id` matches + `storage.crons` entries where `thread_id` matches (best-effort loop over `storage.crons`). Runtime `run_engine.runtime` and its per-thread queues are also pruned best-effort if `app.state.run_engine` exists. Todo 6 owns the real cron registry semantics; this placeholder is intentionally minimal — todo 6 must extend the `cron_id` shape and the scheduler cancellation once the cron engine lands.

### search acceptance of select/sort params

`POST /threads/search` accepts the exact params the suite sends — `metadata` (filter dict), `limit` (1-100, default 10), `offset` (>=0, default 0), plus `select` (list of projection keys), `sort_by` (`thread_id`|`created_at`|`updated_at`), `sort_order` (`asc`|`desc`). Filtering is AND of requested `metadata` plus the policy scope filter via `merge_scope_filter` (policy keys win), then `_record_matches` uses the same `RequireScopeMatch` `$eq`/`$contains` logic the auth helper uses. Sorting and slicing are applied after filtering; `select` projection is honored by returning only the requested keys per item (still valid if the harness only asserts presence/absence). Unknown `select`/`sort_*` values → 422; missing body → 422 with empty treated as `{}`. The benign-ignore clause is satisfied: even if a future harness sends an unexpected `select` field set, the endpoint does not crash.

### TTL and flat-dict contract

TTL is supplied as `ttl: {strategy:"delete", ttl:15}` (minutes) on create; the server computes `expires_at = now + ttl_minutes` ISO-8601 UTC and stores it top-level. Every read/search path purges expired records lazily (`_purge_expired` / `_purge_if_expired`) and returns 404 for an expired id; the sweeper is lazy, not a background task, which is sufficient for the in-memory stage and avoids a scheduler dependency before todo 6. Clock is injectable via `server.threads._now()` (monkeypatchable) so TTL tests use no real sleeps.

Flat-dict contract (critical integration with todo 4): `Storage.threads[tid]` is `{"thread_id":…, "created_at":…, "updated_at":…, "expires_at":…?, "metadata": {…}, **metadata}` — scope-relevant keys (e.g., `user_id`, `resource_kind`, `owner`, `intended_thread`) live top-level, not only under `metadata`, so `server.runs`'s `require_scope_match(storage.threads[tid], scope_filter)` sees them. Non-scope bookkeeping (`created_at` etc.) coexists in the same dict.

### state shape

`GET /threads/{id}/state` (and `?checkpoint_id=`) runs the same `threads.read` scope check, then `await storage.saver.aget_tuple({"configurable":{"thread_id":id,"checkpoint_ns":""}})` (with `checkpoint_id` added if supplied). Empty history → `{"values":{},"next":[],"checkpoint":null,"interrupts":[]}`; populated → `{"values": checkpoint["channel_values"], "next": ..., "checkpoint": ..., "config": ..., "interrupts": ...}`. This is the minimal shape the coach perimeter's projection expects (`values`/`interrupts` present, no `pending_document_op_id`).

### thread-cron cascade placeholder left for todo 6

`DELETE /threads/{id}` iterates `storage.crons` and removes entries where `record.get("thread_id")==deleted_id`. At this point `storage.crons` is an empty dict (todo 6 not yet landed), so the loop is a no-op placeholder with a clear comment. Todo 6 must verify the real cron record field names (`thread_id` vs `cron_id` mapping) and whether a scheduler task must be cancelled; this file's comment explicitly marks the extension point.

### helper minimal note

`server/storage.py` was not structurally changed by this todo's worker beyond the warning-logging narrowing already present from a prior wave (the `is_known` guard + `logger.warning`); no new fields or seams were added. Thread TTL, sweep, and cascade logic lives entirely in `server/threads.py` to keep the seam minimal per the "keep it minimal" instruction.

## 2026-08-22 — todo 5: assistants + store endpoints

### storage.py bare-except narrowed + warning

Fixed the `server/storage.py:create_storage` bare `except Exception` that silently degraded semantic search. Now catches only known embedding-construction failures: `ValueError`, `ImportError`, `RuntimeError`, or any `openai.OpenAIError`/`AuthenticationError`/`PermissionDeniedError` class name, or a string containing `api_key`. Other exceptions bubble. On fallback, `logging.getLogger("MedicalRAG").warning(...)` logs the class and message and falls back to `InMemoryStore(index=None)`. Verified: without `OPENAI_API_KEY` the store falls back with a warning (`OpenAIError: The api_key ...`) and `index_config is None`; a non-known `TypeError` correctly re-raises; `ValueError` falls back. This satisfies `.omo/notepads/oss-agent-server-tag-deploys/issues.md` fix instruction.

### assistants registry shape

`server/assistants.py` builds a static registry from `request.app.state.config.graphs` keys. Each assistant: `{"assistant_id": graph_id, "graph_id": graph_id, "name": graph_id, "metadata": {}, "config": {}, "created_at": <now iso>, "updated_at": <now iso>, "version": 1}`. There is no assistant CRUD; registry is read-only. `POST /assistants/search` parses `graph_id?`, `metadata?`, `limit`/`offset` and AND-merges the requested filter with the policy scope via `merge_scope_filter`. The policy is run via `AuthPolicyEngine.run_policy("assistants","read", ...)` which for real `healthcare_rag/agent/auth.py:239-247` returns `{"graph_id":"coach"}` for non-Studio (members blocked at the perimeter in todo 7, but scoped here). Studio (`kind==StudioUser`) gets `None` and sees both graphs. `GET /assistants/{id}` does a 404 hide when the scope filter does not match `target.graph_id` (via `require_scope_match` + explicit `graph_id` check). Routes exported as `routes: list[Route]` with `POST /assistants/search` and `GET /assistants/{id}`; manifest `/store/namespaces/search` stays 501 (unchanged `UNIMPLEMENTED_PATHS`).

### store routes + semantic search testing

`server/store_routes.py` exports `routes: list[Route]` for `PUT/GET/POST /store/items/search/DELETE /store/items` over the shared `request.app.state.storage.store` (`InMemoryStore`). Each handler validates `namespace` labels (empty or containing `.` → 422), validates `key`/`value`/`limit`/`offset`/`filter`/`query` types (→ 422), runs `AuthPolicyEngine.run_policy("store", action, ...)` with `action` in `put/get/search/delete` (all hit the real `auth.on deny_all` catch-all for members → 403), then delegates to the store: `aput` → 204, `aget` → mapped `{"namespace","key","value","created_at","updated_at"}` or 404, `asearch` → `{"items": [...]}` with the same mapping and `query` honored (semantic index), `adelete` → 204. `GET /store/items` uses SDK dot-joined `namespace` query param (`?namespace=a.b&key=k`) split on `.`; `DELETE` and `PUT` use JSON body; `POST /store/items/search` uses `namespace_prefix` list + `filter?` + `limit/offset/query`. `/store/namespaces/search` remains 501 (manifest regression test).

Semantic-search roundtrip: test puts 3 items with distinct `{"text": "..."}` payloads (`cats meow`, `dogs bark`, `cars drive`) into `["test","semantic"]`, then `POST /store/items/search` with `query="cats"` asserts the first returned item is `cats meow`. When `OPENAI_API_KEY` is present the real embedding path is exercised; when absent the test injects a deterministic `FakeEmbeddings` (vectors `[1,0,0]` for cats/feline/meow, `[0,1,0]` for dogs/bark, `[0,0,1]` for cars) via `store.embeddings = FakeEmbeddings()` and `index_config` with `fields=["$"]` so the semantic ranking logic is still exercised deterministically without network. The test was run mocked (no key in this environment) and passed with fake ranking; a skip path exists for environments where neither real nor mocked embeddings are available, but was not taken.

### verification

`uv run pytest tests/server/test_assistants_store.py -q` → 7 passed (assistants search both/coach-only, get by id 404 hide, store put→get→search including semantic, member store 403, malformed 422/404, manifest 501). Full `uv run pytest tests/server/ -q` → 44 passed. Manual QA via `httpx.ASGITransport` on a minimal app mounting both `routes` lists captured real JSON: Studio search returns both assistants, PUT 204, GET returns `{"text":"cats meow"}`, semantic search returns `cats meow` first, member PUT correctly 403, invalid namespace 422, missing item 404. `uv run ruff check server/assistants.py server/store_routes.py server/storage.py` shows only pre-existing BLE001 noise (same class as `server/threads.py`).
