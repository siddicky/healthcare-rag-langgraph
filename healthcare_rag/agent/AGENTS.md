<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# agent

## Purpose
The "coach" agent platform: a separate LangGraph app (`CoachState` /
`CoachInput` / `CoachOutput` in `state.py`, built by `build.py`) layered on top
of the RAG graph in `healthcare_rag/graph/`. Member-facing chat is one
top-level `create_agent` coach (`coach_agent.py`) fronted by a deterministic,
model-free pre-agent gate (`gate.py`) that only peels off cron delivery,
attachments, red-flag/injection/identifier short-circuits, and erasure —
everything else, including medical questions, falls through to the coach
agent, which decides on its own (via its system prompt) whether to answer
directly or call its `medical_lookup` tool. It adds UI composition,
uploads/document review, reminders backed by a remote cron service, per-user
store data, feedback, erasure, and the auth/perimeter layer that fronts the
LangGraph server for member traffic.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Re-exports `build_coach_graph`, `coach`, and the `CoachState`/`CoachInput`/`CoachOutput` types. |
| `state.py` | `CoachState`/`CoachInput`/`CoachOutput` TypedDicts, `CronWakePayload`. |
| `build.py` | `build_coach_graph()` — wires `coach_gate → {short_circuit, coach_agent, claim_document, review_document, erase_my_data, reminder_delivery} → finalize_coach`. |
| `gate.py` | `coach_gate` — a deterministic, model-free routing node (no LLM call): `cron_wake` → attachment → red-flag/injection/identifier-recall regex short-circuit → erasure phrasing → everything else goes to `coach_agent`. Decides `RouteTarget`. |
| `coach_agent.py` | The single top-level coach: `create_agent`-based tool-calling agent (`build_route_b_agent`, `coach_agent` node), `AgentContext`, `SafeModelResponseMiddleware` (also enforces the medical-lookup mixed-call guard: if the model calls `medical_lookup` alongside any other tool in the same step, the other calls are dropped so `return_direct` exits deterministically), `memory_segment` dynamic prompt, `relay_medical_answer` (`after_agent` hook that turns a terminal `medical_lookup` `ToolMessage` into the reply `AIMessage`), uses the shared `ToolCallLimitMiddleware`. |
| `rag_relay.py` | `relay_question(question, config)` — bridges into the existing healthcare RAG graph (`healthcare_rag.graph.build.build_graph`); called by the `medical_lookup` tool (`tools/medical_lookup.py`). Default mode inherits the parent checkpointer (so the RAG child persists under the calling tool's checkpoint namespace of the same coach thread), `HC_RAG_RELAY_MODE=pipeline` is a degraded per-turn fallback. |
| `compose_ui.py` | `compose_ui` tool + `ComposedNode`/`DataRef` models — validates UI compositions against `static_copy_allowlist` and per-turn DATA envelopes; catalog facts must be `__ref` pointers, never literals. |
| `finalize.py` | `finalize_coach` — final whole-channel projection: scrubs all messages via `safe_message.to_safe_message`, clears `pending_document_op_id`. |
| `safe_message.py` | `to_safe_message` — recursively scrubs PHI out of message content/tool-call args before they leave the graph. |
| `short_circuit.py` | `short_circuit` node — renders an already-classified safety outcome (emergency/injection/out-of-scope/identifier-recall) with no model call. |
| `store_data.py` | Per-user `BaseStore` schema and CRUD: `ScheduleEntry`, `ReminderRecord`, `MetricEntry`, `InjectionLogEntry`, `OpRecord`/`ApprovalEvent` (idempotent tool ops), `UploadRegistryRecord`, `Weekday`; namespace validation (`validate_user_namespace`, `guard_user_write`) and `delete_all_for_user` (erasure). |
| `reminders.py` | Reminder CRUD tools (`create_reminder`, `edit_reminder`, `cancel_reminder`) plus `reminder_delivery` node, `cleanup_user_crons`, `sweep_upload_reservations`, and `CronClient` wiring via `deployment_client`. |
| `cron_client.py` | `CronClient` — HTTP client for the remote cron service; `Cron`/`CronCreate` models, `cron_expression(weekday, time)`, `CronAPIError`/`CronAmbiguousError`. |
| `documents.py` | Upload claim/review flow: `claim_document`, `review_document`, `DocumentProposal`/`DocumentDecision` models, `read_multipart_upload`, `scrub_proposal` (privacy-scrubs extracted fields before they enter the store), `reservation_id`. |
| `uploads.py` | Starlette route handlers `post_upload` / `get_upload_status`; atomic 15-minute upload id reservation (`UPLOAD_TTL_MINUTES`), `internal_headers` for server-to-server calls. |
| `cleanup.py` | `prepare_thread_deletion` / `clear_cleanup_marker` — erasure-adjacent thread cleanup used by the perimeter middleware around thread deletion. |
| `erase.py` | `erase_my_data` node — fail-closed remote cleanup (crons, upload reservations) then privileged owner-store erasure; sets the `erase_confirmation_v1` marker. |
| `memory.py` | `remember_fact_impl` tool + `authenticated_user_id`, `principal_mapping`, `sanitize_memory_field`, `dynamic_prompt` — auth-scoped profile/episodic memory; **not** used by the model-free `cron_wake` route. |
| `features.py` | `is_erase_request` — deterministic erasure-phrasing detection (`ERASE_ACTION` + `ERASE_OBJECT` regexes) used by `gate.py`. |
| `auth.py` | `langgraph_sdk.Auth` handlers: `supabase_bearer` (member bearer-token auth), `deny_all`, per-resource scoping (`create_thread`, `delete_thread`, `create_run`, `create_cron`, studio/internal role checks). |
| `perimeter.py` | Pure request-shape validation for member traffic: `validate_member_request`, `project_state` (response projection/filtering), `PerimeterDenied`. |
| `perimeter_middleware.py` | `MemberPerimeterMiddleware` (Starlette `BaseHTTPMiddleware`) — applies `perimeter.py`'s rules to live requests/responses, handles attachment consumption and thread-deletion cleanup hooks. |
| `http_app.py` | Starlette app wiring: feedback route, upload routes, `MemberPerimeterMiddleware`, CORS, `lifespan`, `internal_version`. |
| `feedback.py` | `post_feedback` route handler — proxies feedback to LangSmith via `Client`, scrubs PHI first. |
| `ns_sweep.py` | `checkpoint_records` / `diff_records` / `lineage_leaves` — checkpoint-namespace inspection helpers (used by erasure/cleanup sweeps). |
| `static_copy_allowlist.py` | `STATIC_COPY_ALLOWLIST` / `DISPATCH_ALLOWLIST` — the fixed set of UI labels/actions `compose_ui` may emit as literals. |
| `cron_client.py`, `store_data.py` | (see above) shared by `tools/`. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `tools/` | The coach agent's domain tools (`change_schedule`, `log_metric`, `log_injection`, `view_schedule`, `medical_lookup`) — see `tools/AGENTS.md`. |

## For AI Agents

### Working In This Directory
- The coach gate is ordered by feature gate: `D0a` is the server-only
  `cron_wake` route (model-free — never calls `memory.py`), `D0b` is the
  attachment route. Member envelopes may contain only `question` plus an
  optional `attachment_id`; resumes have exactly one shape:
  `{accept, fields?}`.
- Medical answers only ever come out of `medical_lookup` (`return_direct`);
  the model never paraphrases them. There is no pre-agent LLM classifier
  anymore — the model decides, from its system prompt, whether a turn is
  medical. Watch `make eval-agent` / multiturn `safety_drift` for the failure
  mode this shifts risk toward: the model answering a drug question from its
  own knowledge instead of calling the tool.
- The coach agent uses the shared built-in `ToolCallLimitMiddleware` for
  `compose_ui` calls — do not add a second ad-hoc interrupt counter.
- Catalog facts in `compose_ui` trees must always be a `__ref` object into a
  same-turn DATA envelope; only static labels/actions may use
  `static_copy_allowlist.py`'s literal allow-list.
- `cron_wake` is server-originated only: the member perimeter (`auth.py`,
  `perimeter.py`) must keep rejecting it, and no member-facing cron-management
  route should be exposed.
- Upload ids are atomically reserved for 15 minutes (`uploads.py`); extracted
  bytes live only in the request-lifetime buffer, and only a
  privacy-scrubbed `DocumentProposal` (via `documents.scrub_proposal`) ever
  enters the store.
- Action-bar eligibility (regenerate/branch/feedback) is turn-local — do not
  make it depend on anything outside the current turn's message list.
- Erasure (`erase.py`) is two-phased: the graph sweeps reminders/crons/upload
  reservations/owner-store data/gate first and emits its marker; the member
  side then snapshots and deletes non-current threads before the
  marker-bearing current thread last. Any earlier failure must be
  fail-stop/retryable, not silently swallowed.
- `SERVER_LOCAL_DEV` (credential-less Studio principal in `auth.py`) is
  dev-only — never let it leak into a prod code path.

### Testing Requirements
- `tests/agent/` mirrors this directory closely: `test_coach_gate.py`
  (`gate.py`), `test_route_b.py` (`coach_agent.py`, including the
  `medical_lookup` round trip and mixed-call guard), `test_rag_relay.py`
  (`rag_relay.py`), `test_documents.py` (`documents.py`), `test_reminders.py`
  (`reminders.py`), `test_store_data.py` (`store_data.py`),
  `test_memory.py` (`memory.py`), `test_features.py` (`features.py`),
  `test_auth.py` (`auth.py`), `test_perimeter_composed.py` /
  `test_perimeter_studio.py` / `test_server_perimeter.py` (`perimeter.py`,
  `perimeter_middleware.py`, `auth.py`), `test_deploy_config.py`,
  `test_finalized_stream.py` / `test_member_streaming.py` (`finalize.py`,
  `http_app.py`).
- Tool-specific tests live alongside but target `tools/` (see that
  directory's AGENTS.md).

### Common Patterns
- Every module that reads the authenticated user does so via
  `authenticated_user_id(config)` (from `memory.py`) or an equivalent
  `_authenticated_user_id` helper duplicated locally in `tools/` modules —
  never trust a client-supplied user id.
- Store writes go through `store_data.py` helpers, which scrub PHI/PII via
  `PrivacySanitizer` before persisting (`_scrub_json`).
- `assert_never` is used pervasively on `Literal`/`StrEnum` matches to keep
  exhaustiveness checked by the type checker.

## Dependencies

### Internal
- `healthcare_rag/graph/` — `medical_lookup` relays into `graph.build.build_graph()` (via `rag_relay.relay_question`) and reuses `graph.resources`.
- `healthcare_rag/processors/safety.py`, `processors/privacy.py` — PHI/PII scrubbing shared with the RAG graph.
- `healthcare_rag/models/safety.py` — `SafetyAssessment`, `SocialIntent`.
- `agent/tools/` — the coach agent's tool set.

### External
- `langchain.agents` (`create_agent`, middleware), `langgraph` (`StateGraph`, `Command`, `interrupt`, `BaseStore`), `langgraph_sdk.Auth`, `starlette` (HTTP app/middleware), `httpx` (cron/internal HTTP calls), `langsmith` (feedback proxy).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
